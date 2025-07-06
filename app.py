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

from db import get_toolset_by_server_id, make_yaml, get_server_url_and_port




app = FastAPI()
DOCKER_IMAGE = "0d7c52e29a52"        # your pre-built image



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
    tools_yaml = make_yaml(get_toolset_by_server_id(server_id))

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

        #server_name = f"toolbox_{uuid.uuid4().hex[:8]}.speakmultiapp.com"
        server_name = server_url.replace("https://", "")
        try:
            add_and_reload_nginx(host_port, server_name)
        except Exception as e:
            print(f"Error adding and reloading nginx: {e}")
            raise HTTPException(500, f"Error adding and reloading nginx: {e}") from e

    #     dep = Deployment(
    #     container_id=container.id,
    #     host_port=host_port,
    #     workdir=workdir,
    #     server_name=server_name,
    #     volumes=volumes,
    # )

        # container_id = container.id[:12]
        # DEPLOYMENTS[container_id] = dep

        # print("container_id: ", container_id)

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




if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8005)