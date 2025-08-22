# deploy_api.py
import shutil, tempfile, uuid, os, zipfile
from pathlib import Path
from typing import Annotated, Optional
from fastapi import FastAPI, UploadFile, File, HTTPException, Form
from docker import from_env as docker_from_env
import docker.errors

from dataclasses import dataclass
from docker.models.containers import Container
from utils import add_and_reload_nginx, remove_server_block

from db import get_all_tools_for_yaml, make_yaml, get_server_url_and_port, get_container_id_by_server_id

from fastapi.responses import PlainTextResponse, JSONResponse


app = FastAPI()
DOCKER_IMAGE = "a5f0ab595509"        # your pre-built image



# ----------------------------------------------------------------------





def extract_hooks(upload: UploadFile, dest: Path):
    """
    Accepts a single .py file (for the MVP).
    Writes it under  <dest>/<maybe_nested_dirs>/<filename>.py
    """
    suffix = Path(upload.filename).suffix.lower()
    if suffix != ".py":
        raise HTTPException(400, f"Unsupported hooks format: {suffix}")

    dest_file = dest / upload.filename           
    dest_file.parent.mkdir(parents=True, exist_ok=True)

    # 2.  Copy bytes
    with open(dest_file, "wb") as out_fp:        
        shutil.copyfileobj(upload.file, out_fp) 






#accept server_id and generate tools.yaml, make blob optional
@app.post("/deploy/{server_id}")
async def deploy(
    server_id: str,
    hooks_blob: Annotated[
        UploadFile | None,                    # ↱ UploadFile is optional
        File(description="Optional hooks code (.py)")
    ] = None,
    #optional
):
    workdir = Path(tempfile.mkdtemp(prefix="toolbox_"))
    plugins_dir = workdir / "plugins"
    plugins_dir.mkdir()
    yaml_path  = workdir / "tools.yaml"

    volumes = {
        str(workdir / "plugins"): {"bind": "/plugins", "mode": "ro"},
        str(workdir / "tools.yaml"): {"bind": "/app/tools.yaml", "mode": "ro"},
    }

    # 1. generate tools.yaml
    db_rows, api_rows = get_all_tools_for_yaml(server_id)
    tools_yaml = make_yaml(db_rows, api_rows)

    server_url, host_port = get_server_url_and_port(server_id)

    # 2. save YAML
    with open(yaml_path, "w") as f:
        f.write(tools_yaml)

    # 3. save/extract hooks
    if hooks_blob:
        extract_hooks(hooks_blob, plugins_dir)

    # 4. run container
    client = docker_from_env()

    try:
        container = client.containers.run(
            DOCKER_IMAGE,
            detach=True,
            name=f"toolbox_{uuid.uuid4().hex[:8]}",
            ports={"8002/tcp": host_port},
            volumes=volumes,
        )

        server_name = server_url.replace("https://", "")
        try:
            add_and_reload_nginx(host_port, server_name)
        except Exception as e:
            print(f"Error adding and reloading nginx: {e}")
            raise HTTPException(500, f"Error adding and reloading nginx: {e}") from e



    except docker.errors.ContainerError as e:
        shutil.rmtree(workdir)
        raise HTTPException(500, f"Docker run failed: {e.explanation}") from e

    return {
        "container_id": container.id,
        "status_url": f"https://{server_name}/health",
        "workdir": workdir,
        "volumes": volumes,
        "server_name": server_name,
        "host_port": host_port
    }


@app.post("/stop/{cid}")
async def stop_container(cid: str, conf: dict):
    # dep = DEPLOYMENTS.get(cid)
    # if not dep:
    #     raise HTTPException(404, f"Unknown container {cid}")

    client = docker_from_env()
    try:
        cont: Container = client.containers.get(cid)
        cont.stop(timeout=10)
        cont.remove()
    except docker.errors.NotFound:
        pass  # already gone

    try:
        remove_server_block(conf["server_name"])
    except Exception as e:
        print(f"Error removing server block: {e}")
        raise HTTPException(500, f"Error removing server block: {e}") from e

    # cleanup filesystem assets
    shutil.rmtree(conf["workdir"], ignore_errors=True)
    # DEPLOYMENTS.pop(cid, None)

    return {"status": "stopped", "container_id": cid}


@app.get(
    "/logs/{server_id}",
    response_class=PlainTextResponse,
    summary="Fetch Docker logs for a deployed server",
)
async def get_logs(
    server_id: str,
    tail: int = 200,                   
):
    """
    Return the last *tail* lines from the Docker container tied to **server_id**.

    * Looks up *container_id* in DB (`get_container_id_by_server_id`).
    * Calls `docker logs --tail <tail>`.
    * Returns plain text (so `kubectl logs` style).
    """
    container_id = get_container_id_by_server_id(server_id)
    if container_id is None:
        raise HTTPException(404, f"No deployment found for server {server_id}")

    client = docker_from_env()
    try:
        cont: Container = client.containers.get(container_id)
    except docker.errors.NotFound:
        raise HTTPException(404, f"Container {container_id} not running")

    try:
        logs_bytes = cont.logs(tail=tail)
    except docker.errors.APIError as e:
        raise HTTPException(500, f"Docker logs failed: {e.explanation}") from e

    return logs_bytes.decode("utf-8", errors="replace")


# ----------------------------------------------------------------------


# ----------------------------------------------------------------------
@app.get(
    "/health/{server_id}",
    response_class=JSONResponse,
    summary="Check the health / runtime state of a deployed server",
)
async def container_health(server_id: str):
    """
    Return runtime status for the Docker container linked to *server_id*.

    Response schema
    ---------------
    {
      "server_id":     "<uuid>",
      "container_id":  "<12-char id>",
      "running":        true | false,
      "state":          "running" | "exited" | "paused" | ...,
      "health":         "healthy" | "unhealthy" | null   # requires HEALTHCHECK
    }
    """
    container_id = get_container_id_by_server_id(server_id)
    if container_id is None:
        return {
            "server_id":    server_id,
            "container_id": container_id,
            "running":      False,
            "state":        "not_found",
            "health":       None,
        }

    client = docker_from_env()
    try:
        cont: Container = client.containers.get(container_id)
    except docker.errors.NotFound:
        # Entry in DB but Docker doesn’t have it (crashed / pruned)
        return {
            "server_id":    server_id,
            "container_id": container_id,
            "running":      False,
            "state":        "not_found",
            "health":       None,
        }

    state = cont.attrs["State"]              # raw “docker inspect” state block
    running     = state["Running"]
    status_str  = state["Status"]            # running / exited / restarting / …
    health_info = state.get("Health")
    health_str  = health_info["Status"] if health_info else None  # healthy / unhealthy

    return {
        "server_id":    server_id,
        "container_id": container_id[:12],
        "running":      running,
        "state":        status_str,
        "health":       health_str,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8005)