import re
import subprocess
from pathlib import Path
from typing import List


DOMAIN_BASE = "getoneplace.ai"
CERT_PATH = "/etc/letsencrypt/live/getoneplace.ai-0001/fullchain.pem"
KEY_PATH = "/etc/letsencrypt/live/getoneplace.ai-0001/privkey.pem"
CONF_FILE = Path("/etc/nginx/sites-available/getoneplace-mcp")
ENABLED_FILE = Path("/etc/nginx/sites-enabled/getoneplace-mcp")


def normalize_server_name(server_name: str) -> str:
    """Map legacy MCP hostnames onto the getoneplace.ai domain."""
    host = server_name.strip().lower()
    host = re.sub(r"^https?://", "", host).split("/", 1)[0]

    legacy_suffixes = (".speakmulti.com", ".speakmultiapp.com")
    for suffix in legacy_suffixes:
        if host.endswith(suffix):
            return host[: -len(suffix)] + f".{DOMAIN_BASE}"

    return host


def add_and_reload_nginx(port: int, server_name: str) -> None:
    """
    Append a server block for <server_name> → localhost:<port> to the main
    config file, validate it with `nginx -t`, and reload nginx.

    Raises
    ------
    RuntimeError
        If nginx syntax check fails or reload fails.
    PermissionError
        If the function is not run with sufficient privileges.
    """

    server_name = normalize_server_name(server_name)

    CONF_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not CONF_FILE.exists():
        CONF_FILE.write_text("# Dynamic OnePlace MCP server blocks\n")
    if not ENABLED_FILE.exists():
        ENABLED_FILE.symlink_to(CONF_FILE)

    remove_server_block(server_name, reload_nginx=False)

    # 1 ▸ Build the block
    block = f"""
server {{
    listen 80;
    listen [::]:80;
    server_name {server_name};

    return 301 https://$host$request_uri;
}}

server {{
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name {server_name};

    ssl_certificate     {CERT_PATH};
    ssl_certificate_key {KEY_PATH};
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;

    location / {{
        proxy_pass http://127.0.0.1:{port};
        proxy_set_header Host               $host;
        proxy_set_header X-Real-IP          $remote_addr;
        proxy_set_header X-Forwarded-For    $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto  https;
        proxy_set_header X-Forwarded-Port   443;

        proxy_http_version 1.1;
        proxy_set_header  Connection        '';
        proxy_buffering   off;
        proxy_cache       off;
        proxy_set_header  X-Accel-Buffering no;
        proxy_read_timeout 86400;
    }}
}}
"""

    # 2 ▸ Append to the config file
    try:
        CONF_FILE.write_text(CONF_FILE.read_text() + "\n" + block)
    except PermissionError:
        raise PermissionError(
            f"Cannot write to {CONF_FILE}. Run the script with sudo or as root."
        )

    # 3 ▸ Check syntax
    print("→ running `nginx -t` …")
    result = subprocess.run(
        ["nginx", "-t"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        # Revert the change if syntax fails
        CONF_FILE.write_text(CONF_FILE.read_text().replace(block, ""))
        raise RuntimeError(f"nginx -t failed:\n{result.stderr}")

    # 4 ▸ Reload nginx
    print("→ reloading nginx …")
    result = subprocess.run(
        ["systemctl", "reload", "nginx"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to reload nginx:\n{result.stderr}")

    print(f"✓ Added {server_name} → localhost:{port} and reloaded nginx.")


def _split_server_blocks(text: str) -> List[str]:
    """
    Very small brace-counter that splits an nginx config into top-level
    `server { … }` blocks.  Everything outside those blocks is returned as a
    separate fragment so we can re-assemble the file later.
    """
    blocks, buf, depth = [], [], 0
    for line in text.splitlines(keepends=True):
        # Track braces
        opens = line.count("{")
        closes = line.count("}")
        depth += opens - closes

        buf.append(line)

        # Close of a top-level server-block
        if depth == 0 and buf:
            blocks.append("".join(buf))
            buf = []

    # Any trailing whitespace etc.
    if buf:
        blocks.append("".join(buf))
    return blocks


def remove_server_block(server_name: str, reload_nginx: bool = True) -> None:
    """
    Remove every server-block that has `server_name <server_name>` (substring
    match), validate config, and reload nginx.

    Raises
    ------
    RuntimeError   if nginx syntax test or reload fails
    FileNotFoundError / PermissionError on IO problems
    """
    server_name = normalize_server_name(server_name)
    if not CONF_FILE.exists():
        return

    original = CONF_FILE.read_text()

    # ---- 1 ▸ split file into chunks -----------------------------------------
    parts = _split_server_blocks(original)

    # ---- 2 ▸ filter out the blocks we want to delete -------------------------
    pattern = re.compile(r"\bserver_name\s+.*\b" + re.escape(server_name) + r"\b")
    kept_parts = [
        blk
        for blk in parts
        if not (blk.lstrip().startswith("server") and pattern.search(blk))
    ]
    if len(kept_parts) == len(parts):
        print(f"No server block mentioning '{server_name}' found – nothing changed.")
        return

    new_conf = "".join(kept_parts)
    CONF_FILE.write_text(new_conf)

    if not reload_nginx:
        return

    # ---- 3 ▸ syntax check ----------------------------------------------------
    print("→ running `nginx -t` …")
    test = subprocess.run(["nginx", "-t"], capture_output=True, text=True)
    if test.returncode != 0:
        CONF_FILE.write_text(original)  # rollback
        raise RuntimeError(f"nginx -t failed:\n{test.stderr}")

    # ---- 4 ▸ reload nginx ----------------------------------------------------
    print("→ reloading nginx …")
    reload = subprocess.run(
        ["systemctl", "reload", "nginx"], capture_output=True, text=True
    )
    if reload.returncode != 0:
        raise RuntimeError(f"Failed to reload nginx:\n{reload.stderr}")

    print(f"✓ Removed server block(s) for {server_name} and reloaded nginx.")
