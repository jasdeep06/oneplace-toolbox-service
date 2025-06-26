# deploy_api.py
import shutil, tempfile, uuid, os, zipfile
from pathlib import Path
from typing import Annotated
from fastapi import FastAPI, UploadFile, File, HTTPException, Form
from docker import from_env as docker_from_env
import docker.errors

from dataclasses import dataclass
from docker.models.containers import Container



app = FastAPI()
DOCKER_IMAGE = "9e9869f196"        # your pre-built image
HOST_PORT_POOL = range(8100, 9000)     # pick whatever range you like
_port_iter = iter(HOST_PORT_POOL)      # naive allocator – replace with DB if multi-node


# ----------------------------------------------------------------------
@dataclass
class Deployment:
    container_id: str
    host_port:    int
    workdir:      Path                # holds plugins/ and tools.yaml
    volumes:      dict                # docker-py volume dict we used
    image:        str = DOCKER_IMAGE  # default

DEPLOYMENTS: dict[str, Deployment] = {}



def next_port() -> int:
    try:
        return next(_port_iter)
    except StopIteration:
        raise RuntimeError("Port pool exhausted")

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

@app.post("/deploy")
async def deploy(
    tools_yaml: Annotated[UploadFile, File(description="YAML file")],
    hooks_blob: Annotated[UploadFile, File(description="hooks code (py)")],
):
    workdir = Path(tempfile.mkdtemp(prefix="toolbox_"))
    plugins_dir = workdir / "plugins"
    plugins_dir.mkdir()
    yaml_path  = workdir / "tools.yaml"

    # 1. save YAML
    with open(yaml_path, "wb") as f:
        shutil.copyfileobj(tools_yaml.file, f)

    # 2. save/extract hooks
    extract_hooks(hooks_blob, plugins_dir)

    # 3. run container
    client = docker_from_env()
    host_port = next_port()
    try:
        container = client.containers.run(
            DOCKER_IMAGE,
            detach=True,
            name=f"toolbox_{uuid.uuid4().hex[:8]}",
            ports={"8002/tcp": host_port},
            volumes={
                str(plugins_dir): {"bind": "/plugins", "mode": "ro"},
                str(yaml_path):  {"bind": "/app/tools.yaml", "mode": "ro"},
            },
            # any env overrides here
        )

        dep = Deployment(
        container_id=container.id,
        host_port=host_port,
        workdir=workdir,
        volumes={
            str(workdir / "plugins"): {"bind": "/plugins", "mode": "ro"},
            str(workdir / "tools.yaml"): {"bind": "/app/tools.yaml", "mode": "ro"},
        },
    )
        DEPLOYMENTS[container.id] = dep

        print("container_id: ", container.id)

    except docker.errors.ContainerError as e:
        shutil.rmtree(workdir)
        raise HTTPException(500, f"Docker run failed: {e.explanation}") from e

    return {
        "container_id": container.id[:12],
        "host_port": host_port,
        "status_url": f"http://{os.getenv('PUBLIC_HOST', 'localhost')}:{host_port}/health",
    }

@app.post("/stop/{cid}")
async def stop_container(cid: str):
    dep = DEPLOYMENTS.get(cid)
    if not dep:
        raise HTTPException(404, f"Unknown container {cid}")

    client = docker_from_env()
    try:
        cont: Container = client.containers.get(cid)
        cont.stop(timeout=10)
        cont.remove()
    except docker.errors.NotFound:
        pass  # already gone

    # cleanup filesystem assets
    shutil.rmtree(dep.workdir, ignore_errors=True)
    DEPLOYMENTS.pop(cid, None)

    return {"status": "stopped", "container_id": cid}


@app.post("/restart/{cid}")
async def restart_container(cid: str):
    dep = DEPLOYMENTS.get(cid)
    if not dep:
        raise HTTPException(404, f"Unknown container {cid}")

    client = docker_from_env()

    # 1. ensure old container is gone (idempotent)
    try:
        old = client.containers.get(cid)
        old.stop(timeout=10)
        old.remove()
    except docker.errors.NotFound:
        pass

    # 2. launch a **new** container on the SAME host_port and with SAME volumes
    new_id = f"toolbox_{uuid.uuid4().hex[:8]}"
    container = client.containers.run(
        dep.image,
        detach=True,
        name=new_id,
        ports={"8002/tcp": dep.host_port},
        volumes=dep.volumes,
    )

    # 3. update registry
    DEPLOYMENTS.pop(cid)
    dep.container_id = container.id
    DEPLOYMENTS[container.id] = dep

    return {
        "status": "restarted",
        "old_container": cid,
        "new_container": container.id[:12],
        "host_port": dep.host_port,
    }



if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)