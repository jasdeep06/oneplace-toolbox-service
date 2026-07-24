# OnePlace toolbox deployment service

This FastAPI service turns stored tool definitions into MCP toolbox runtime
containers. It generates toolbox configuration, assigns the database-backed
host port, starts or stops Docker containers, and updates the Nginx proxy.

## Main operations

- `POST /deploy/{server_id}` builds and starts a toolbox server.
- `POST /stop/{cid}` stops a deployed toolbox container.
- Read endpoints expose generated configuration and deployment information;
  use `/docs` for the current OpenAPI contract.

## Local setup

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
./start.sh
```

The primary service listens on port 8005; Cortex uses port 8025. It requires
Docker socket access, the OnePlace PostgreSQL database, and permission to
manage the intended Nginx configuration. Treat those privileges as
production-sensitive.
