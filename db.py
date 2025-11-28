from __future__ import annotations
from socket import timeout
from psycopg2.extras import RealDictCursor


BASE_URL = "https://oneplace-api.speakmulti.com/"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMmM4Nzg5OC03MGMwLTQzMjAtOTZiZi1hMjI0ZWY5ZjI3NjMifQ.3HxEAI26XSq1S-tJCS1pOXsvjexGuAARqCy_vj5LiAo"

query = """
SELECT
    ms.id                     AS server_id,
    ms.server_url             AS server_url,
    ts.id                     AS toolset_id,
    ts.name                   AS toolset_name,
    t.id                      AS tool_id,
    t.name                    AS tool_name,
	t.description			   AS tool_description,
    d.id                      AS datasource_id,
    d.name                    AS datasource_name,
	c.config_params           AS connection_params,
	c.id					  AS connection_id,
	c.name					  AS connection_name,
	dbt.name             AS  kind,
	dbtm.parameters      AS    tool_params,
	dbtm.sql_query      AS    sql_query
FROM           mcp_server                AS ms
JOIN           mcp_server_toolset_link   AS mstl  ON mstl.mcp_server_id = ms.id
JOIN           toolset                   AS ts    ON ts.id            = mstl.toolset_id
JOIN           toolset_tool_link         AS ttl   ON ttl.toolset_id   = ts.id
JOIN           tool                      AS t     ON t.id             = ttl.tool_id
JOIN           db_tool_metadata          AS dbtm  ON t.id           =   dbtm.tool_id
LEFT JOIN      tool_datasource_link      AS tdl   ON tdl.tool_id      = t.id
LEFT JOIN      datasource                AS d     ON d.id            = tdl.datasource_id
LEFT JOIN		connection				  AS c     ON c.id			  = d.connection_id
LEFT JOIN       db_type					  AS dbt   ON dbt.id         = c.db_type
WHERE ms.id = %(server_id)s AND t.type = 'DATABASE_TOOL'
ORDER BY ts.name, t.name, d.name;        
"""

# Query API tools as JSON blobs, parameterized by server_id
api_tools_query = """
SELECT
    json_build_object(
        'id',           t.id::text,
        'name',         t.name,
        'description',  t.description,
        'tool_type',    t.type,

        /* ---------- HTTP-connection summary ------------- */
        'connection_name', hc.name,
        'connection_id',   hc.id::text,
        'base_url',        hc.base_url,

        /* ---------- API metadata ------------------------ */
        'method',       atm.method,
        'path',         atm.path,
        'path_params',  atm.path_params,
        'query_params', atm.query_params,
        'body_params',  atm.body_params,
        'body',         atm.body,
        'inherit_auth', atm.inherit_auth,

        /* resolved auth (inherit ⇒ take from connection) */
        'auth_type',
            CASE WHEN atm.inherit_auth THEN hc.auth_type ELSE atm.auth_type END,
        'auth_config',
            CASE WHEN atm.inherit_auth THEN hc.auth_config ELSE atm.auth_config END,

        /* headers always come from the connection */
        'headers',      hc.headers,

        /* toolset membership for YAML grouping */
        'toolset_name', ts.name
    ) AS api_tool_detail
FROM           mcp_server              AS ms
JOIN           mcp_server_toolset_link AS mstl  ON mstl.mcp_server_id = ms.id
JOIN           toolset                 AS ts    ON ts.id             = mstl.toolset_id
JOIN           toolset_tool_link       AS ttl   ON ttl.toolset_id    = ts.id
JOIN           tool                    AS t     ON t.id              = ttl.tool_id
JOIN           api_tool_metadata       AS atm   ON atm.tool_id       = t.id
JOIN           http_connection         AS hc    ON hc.id             = atm.http_connection_id
WHERE ms.id = %(server_id)s
  AND t.type = 'API_TOOL'
ORDER BY ts.name, t.name;
"""

rag_tool_query = """
SELECT
    json_build_object(
        'id',           t.id::text,
        'name',         t.name,
        'description',  t.description,
        'tool_type',    t.type,
		'collection_id', hc.id,
		'collection_name' , hc.name,
		'workspace_id',  hc.workspace_id,
        /* toolset membership for YAML grouping */
        'toolset_name', ts.name
    ) AS rag_tool_detail
FROM           mcp_server              AS ms
JOIN           mcp_server_toolset_link AS mstl  ON mstl.mcp_server_id = ms.id
JOIN           toolset                 AS ts    ON ts.id             = mstl.toolset_id
JOIN           toolset_tool_link       AS ttl   ON ttl.toolset_id    = ts.id
JOIN           tool                    AS t     ON t.id              = ttl.tool_id
JOIN           rag_tool_metadata       AS rtm   ON rtm.tool_id       = t.id
JOIN           collection         AS hc    ON hc.id             = rtm.collection_id
WHERE ms.id = %(server_id)s
  AND t.type = 'RAG_TOOL'
ORDER BY ts.name, t.name;

"""

nlq_tools_query = """

SELECT
    json_build_object(
        'id',           t.id::text,
        'name',         t.name,
        'description',  t.description,
        'tool_type',    t.type,
		'domain_id', hc.id,
		'domain_name' , hc.name,
		'workspace_id',  hc.workspace_id,
        /* toolset membership for YAML grouping */
        'toolset_name', ts.name
    ) AS rag_tool_detail
FROM           mcp_server              AS ms
JOIN           mcp_server_toolset_link AS mstl  ON mstl.mcp_server_id = ms.id
JOIN           toolset                 AS ts    ON ts.id             = mstl.toolset_id
JOIN           toolset_tool_link       AS ttl   ON ttl.toolset_id    = ts.id
JOIN           tool                    AS t     ON t.id              = ttl.tool_id
JOIN           nlq_tool_metadata       AS ntm   ON ntm.tool_id       = t.id
JOIN           domain         AS hc    ON hc.id             = ntm.domain_id
WHERE ms.id = %(server_id)s
  AND t.type = 'NLQ_TOOL'
ORDER BY ts.name, t.name;


"""


def get_api_tools_by_server_id(server_id: str) -> list[dict]:
    with psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor) as conn:
        with conn.cursor() as cur:
            cur.execute(api_tools_query, {"server_id": server_id})
            rows = cur.fetchall()
            out = []
            for r in rows:
                j = r.get("api_tool_detail")
                if isinstance(j, str):
                    try:
                        j = json.loads(j)
                    except Exception:
                        continue
                if isinstance(j, dict):
                    out.append(j)
            print(out)
            return out


def get_rag_tools_by_server_id(server_id: str) -> list[dict]:
    with psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor) as conn:
        with conn.cursor() as cur:
            cur.execute(rag_tool_query, {"server_id": server_id})
            rows = cur.fetchall()
            out = []
            for r in rows:
                j = r.get("rag_tool_detail")
                if isinstance(j, str):
                    try:
                        j = json.loads(j)
                    except Exception:
                        continue
                if isinstance(j, dict):
                    out.append(j)
            return out


def get_nlq_tools_by_server_id(server_id: str) -> list[dict]:
    with psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor) as conn:
        with conn.cursor() as cur:
            cur.execute(nlq_tools_query, {"server_id": server_id})
            rows = cur.fetchall()
            out = []
            for r in rows:
                # note: the SELECT aliases it as rag_tool_detail
                j = r.get("rag_tool_detail")
                if isinstance(j, str):
                    try:
                        j = json.loads(j)
                    except Exception:
                        continue
                if isinstance(j, dict):
                    out.append(j)
            return out


from collections import OrderedDict, defaultdict
import json, re, yaml
import base64

from yaml.representer import SafeRepresenter
import yaml, collections


class _Lit(str):
    pass


yaml.add_representer(
    _Lit,
    lambda dumper, data: dumper.represent_scalar(
        "tag:yaml.org,2002:str", data, style="|"
    ),
    Dumper=yaml.SafeDumper,
)

yaml.add_representer(
    collections.OrderedDict,
    SafeRepresenter.represent_dict,  # treat it like a normal dict
    Dumper=yaml.SafeDumper,  # register it for safe_dump
)


_slug_rx = re.compile(r"[^a-z0-9]+")


def _slug(text: str) -> str:
    return _slug_rx.sub("-", text.lower()).strip("-")


def _convert_sql_named_to_positional(sql: str, param_defs) -> str:
    """Convert %(name)s style placeholders to $1, $2 ... based on param order.

    - Uses the order of items in param_defs (list of dicts with 'name') to assign indices.
    - Repeated names map to the same index.
    - Unknown names (not present in param_defs) are assigned indices after known ones,
      in order of first appearance.
    - If the SQL already uses $-style placeholders, this function leaves them untouched.
    """
    if not sql:
        return sql

    try:
        names_in_order = [
            p.get("name")
            for p in (param_defs or [])
            if isinstance(p, dict) and p.get("name")
        ]
    except Exception:
        names_in_order = []

    name_to_idx: dict[str, int] = {}
    # Assign indices for known names by declared order first
    for i, nm in enumerate(names_in_order, start=1):
        if nm not in name_to_idx:
            name_to_idx[nm] = i

    next_idx = len(name_to_idx) + 1

    # Regex for %(name)s / %(name)d / %(name)f etc
    pattern = re.compile(r"%\((?P<name>[A-Za-z_][A-Za-z0-9_]*)\)[a-zA-Z]")

    def repl(m: re.Match):
        nm = m.group("name")
        nonlocal next_idx
        if nm not in name_to_idx:
            name_to_idx[nm] = next_idx
            next_idx += 1
        return f"${name_to_idx[nm]}"

    return pattern.sub(repl, sql)


def make_yaml(
    rows: list[dict],
    api_tools: list[dict] | None = None,
    rag_tools: list[dict] | None = None,
    nlq_tools: list[dict] | None = None,
) -> str:
    if api_tools is None:
        api_tools = []
    if rag_tools is None:
        rag_tools = []
    if nlq_tools is None:
        nlq_tools = []

    # Compute auth headers helper (only supports header location)
    def _auth_headers_for(auth_type: str | None, cfg: dict | None) -> dict:
        if not auth_type or not cfg:
            return {}
        auth_type = (auth_type or "").upper()
        # loc = (cfg.get("location") or "").lower()
        # if loc != "header":
        #     return {}

        if auth_type == "API_KEY":
            name = cfg.get("name")
            val = cfg.get("value")
            return {name: val} if name and val is not None else {}
        if auth_type == "BEARER":
            tok = cfg.get("access_token")
            return {"Authorization": f"Bearer {tok}"} if tok else {}
        if auth_type == "BASIC":
            u, p = cfg.get("username"), cfg.get("password")
            if u is not None and p is not None:
                b64 = base64.b64encode(f"{u}:{p}".encode("utf-8")).decode("ascii")
                return {"Authorization": f"Basic {b64}"}
        return {}

    # Aggregate connection-level auth headers when tools inherit auth
    conn_auth_headers: dict[str, dict] = defaultdict(dict)
    for t in api_tools:
        print("t ", t)
        if t.get("inherit_auth"):
            ah = _auth_headers_for(t.get("auth_type"), t.get("auth_config") or {})
            print("ah ", ah)
            if ah:
                conn_auth_headers[t["connection_id"]].update(ah)

    # ---------- 1. SOURCES ---------------------------------------------------
    src_by_conn: dict[str, str] = {}
    sources_od: OrderedDict[str, dict] = OrderedDict()

    # 1a. Database connections
    for row in rows:
        conn_id = row["connection_id"]
        if conn_id in src_by_conn:
            continue

        cfg = row["connection_params"]
        key = _slug(row["connection_name"] or f"conn-{len(src_by_conn) + 1}")
        src_by_conn[conn_id] = key

        sources_od[key] = OrderedDict(
            # convert to lowercase
            kind=row["kind"].lower(),
            host=cfg["host"],
            port=int(cfg.get("port", 5432)),
            database=cfg["database"],
            user=cfg["username"],
            password=cfg["password"],
        )

    # 1b. HTTP connections (API sources)
    for t in api_tools:
        conn_id = t["connection_id"]
        if conn_id in src_by_conn:
            continue

        key = _slug(t.get("connection_name") or f"http-{len(src_by_conn) + 1}")
        # avoid name collisions with DB sources if any
        while key in sources_od:
            key = f"{key}-http"

        src_by_conn[conn_id] = key

        http_src = OrderedDict(kind="http", baseUrl=t["base_url"], timeout="120s")
        headers = (t.get("headers") or {}).copy()
        # append any resolved connection-level auth headers
        if conn_id in conn_auth_headers:
            headers.update(conn_auth_headers[conn_id])
        if headers:
            http_src["headers"] = headers
        # If timeout is later added to query: if t.get("timeout"): http_src["timeout"] = t["timeout"]

        sources_od[key] = http_src

    rag_src_key = None
    if rag_tools or nlq_tools:
        rag_src_key = "rag-api"
        while rag_src_key in sources_od:
            rag_src_key = f"{rag_src_key}-http"

        sources_od[rag_src_key] = OrderedDict(
            kind="http",
            baseUrl=BASE_URL.rstrip("/"),
            timeout="120s",
            headers={
                "Authorization": f"Bearer {TOKEN}",
                "Content-Type": "application/json",
            },
        )

    # ---------- 1c. METADATA SOURCE (static) -------------------------------
    metadata_source = OrderedDict(
        host="ep-ancient-shape-a1kjibq3-pooler.ap-southeast-1.aws.neon.tech",
        port=5432,
        database="neondb",
        user="neondb_owner",
        password="npg_OqZYgaH46CQb",
    )

    # ---------- 2. TOOLS -----------------------------------------------------
    tools_od: OrderedDict[str, dict] = OrderedDict()
    ds_for_tool: defaultdict[str, set[str]] = defaultdict(set)

    # 2a. DB tools
    for row in rows:
        tool_key = _slug(row["tool_name"])
        src_key = src_by_conn[row["connection_id"]]
        kind = f"{row['kind']}-sql" if not row["kind"].endswith("-sql") else row["kind"]

        # collect datasource ids
        if row["datasource_id"]:
            ds_for_tool[tool_key].add(row["datasource_id"])

        if tool_key in tools_od:
            continue

        try:
            params_json = row["tool_params"]
        except Exception:
            params_json = []

        converted_sql = _convert_sql_named_to_positional(row["sql_query"], params_json)
        tools_od[tool_key] = OrderedDict(
            kind=kind.lower(),
            source=src_key,
            description=row["tool_description"],
            parameters=params_json,
            statement=(converted_sql or "").rstrip() + "\n",
        )

    # 2b. API tools (HTTP)
    for t in api_tools:
        tool_key = _slug(t["name"])
        if tool_key in tools_od:
            continue

        src_key = src_by_conn[t["connection_id"]]
        # format path params: /x/{id} -> /x/{{.id}}
        path_val = t.get("path") or ""
        if t.get("path_params"):
            path_val = re.sub(r"\{([A-Za-z0-9_]+)\}", r"{{.\1}}", path_val)

        http_tool = OrderedDict(
            kind="http",
            source=src_key,
            description=t.get("description"),
            path=path_val,
            method=t["method"],
        )
        if t.get("path_params"):
            http_tool["pathParams"] = t["path_params"]
        if t.get("query_params"):
            http_tool["queryParams"] = t["query_params"]
        if t.get("body_params"):
            http_tool["bodyParams"] = t["body_params"]
        if t.get("body") is not None:
            http_tool["requestBody"] = _Lit(t["body"])

        # If auth is not inherited, attach per-tool auth headers
        if not t.get("inherit_auth"):
            th = _auth_headers_for(t.get("auth_type"), t.get("auth_config") or {})
            if th:
                http_tool["headers"] = th

        tools_od[tool_key] = http_tool
        print("rag tools ", rag_tools)

    if rag_tools:
        for t in rag_tools:
            tool_key = _slug(t["name"])
            if tool_key in tools_od:
                continue

            path_val = (
                f"/workspace/{t['workspace_id']}/collection/{t['collection_id']}/query"
            )

            http_tool = OrderedDict(
                kind="http",
                source=rag_src_key,
                description=t.get("description"),
                path=path_val,
                method="POST",
                bodyParams=[
                    {
                        "name": "query",
                        "type": "string",
                        "description": "User question",
                        "required": True,
                    },
                    {
                        "name": "answer_style",
                        "type": "string",
                        "description": "Answer style: detailed or concise",
                        "required": False,
                    },
                    {
                        "name": "require_citations",
                        "type": "boolean",
                        "description": "Whether to include citations",
                        "required": False,
                    },
                ],
            )
            tools_od[tool_key] = http_tool

    if nlq_tools:
        for t in nlq_tools:
            tool_key = _slug(t["name"])
            if tool_key in tools_od:
                continue

            path_val = f"/workspace/{t['workspace_id']}/domain/{t['domain_id']}/op_chat/ask_database"

            http_tool = OrderedDict(
                kind="http",
                source=rag_src_key,
                description=t.get("description"),
                path=path_val,
                method="POST",
                bodyParams=[
                    {
                        "name": "question",
                        "type": "string",
                        "description": "User question",
                        "required": True,
                    },
                ],
            )
            tools_od[tool_key] = http_tool

    # attach comma-separated datasource IDs for DB tools
    for tkey, ids in ds_for_tool.items():
        tools_od[tkey]["datasource_ids"] = ",".join(sorted(ids))

    # ---------- 3. TOOLSETS --------------------------------------------------
    toolsets_od: OrderedDict[str, list[str]] = OrderedDict()

    # DB tool memberships
    for row in rows:
        ts_key = _slug(row["toolset_name"])
        tool_key = _slug(row["tool_name"])
        toolsets_od.setdefault(ts_key, []).append(tool_key)

    # API tool memberships
    for t in api_tools:
        ts_key = _slug(t["toolset_name"])
        tool_key = _slug(t["name"])
        toolsets_od.setdefault(ts_key, []).append(tool_key)

    for t in rag_tools:
        ts_key = _slug(t["toolset_name"])
        tool_key = _slug(t["name"])
        toolsets_od.setdefault(ts_key, []).append(tool_key)

    for t in nlq_tools:
        ts_key = _slug(t["toolset_name"])
        tool_key = _slug(t["name"])
        toolsets_od.setdefault(ts_key, []).append(tool_key)

    # de-dup per toolset
    for ts_key, lst in toolsets_od.items():
        seen = set()
        toolsets_od[ts_key] = [x for x in lst if not (x in seen or seen.add(x))]

    # ---------- 4. Assemble YAML tree ---------------------------------------
    doc = OrderedDict(
        sources=sources_od,
        metadata_source=metadata_source,
        tools=tools_od,
        toolsets=toolsets_od,
    )

    for t in tools_od.values():
        if "statement" in t:
            t["statement"] = _Lit(t["statement"])

    return yaml.safe_dump(doc, sort_keys=False, default_flow_style=False)


# Usage:
# rows = [dict(r) for r in session.exec(stmt).all()]
# print(make_yaml(rows))


# fire query using psycopg2
import psycopg2
import os

DATABASE_URL = "postgresql://postgres:postgres@localhost:5433/oneplace_core"


def get_toolset_by_server_id(server_id: str):
    """Return a list of dicts, one per (toolset, tool, datasource) row."""
    with psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor) as conn:
        with conn.cursor() as cur:
            cur.execute(query, {"server_id": server_id})  # parameterized!
            return cur.fetchall()


port_query = """
SELECT
    ms.server_url             AS server_url,
    ms.port                   AS port
FROM           mcp_server                AS ms
WHERE ms.id = %(server_id)s
"""


def get_server_url_and_port(server_id: str):
    with psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor) as conn:
        with conn.cursor() as cur:
            cur.execute(port_query, {"server_id": server_id})
            results = cur.fetchone()
            return results["server_url"], results["port"]


container_id_query = """
SELECT
    ms.image_details             AS image_details
FROM           mcp_server                AS ms
WHERE ms.id = %(server_id)s
"""


def get_container_id_by_server_id(server_id: str):
    with psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor) as conn:
        with conn.cursor() as cur:
            cur.execute(container_id_query, {"server_id": server_id})
            results = cur.fetchone()
            image_details = results["image_details"]
            if "container_id" in image_details:
                return image_details["container_id"]
            else:
                return None


def get_all_tools_for_yaml(server_id: str):
    db_rows = get_toolset_by_server_id(server_id)
    print("db rows ", db_rows)
    api_rows = get_api_tools_by_server_id(server_id)
    rag_rows = get_rag_tools_by_server_id(server_id)
    nlq_rows = get_nlq_tools_by_server_id(server_id)
    print("rag rows ", rag_rows)
    return db_rows, api_rows, rag_rows, nlq_rows


# if __name__ == "__main__":
#     db_rows, api_rows = get_all_tools_for_yaml('7e9f56cd-7c04-4ed8-b148-496e3d7794ae')
#     print(make_yaml(db_rows, api_rows))

# # # Example:
db_rows, api_rows, rag_rows, nlq_rows = get_all_tools_for_yaml(
    "d064901c-5afc-480f-a37f-313ae2e5676f"
)
print(make_yaml(db_rows, api_rows, rag_rows, nlq_rows))

# print(get_container_id_by_server_id('1dd10264-432d-411f-95f2-4b3cff101471'))

# print(get_rag_tools_by_server_id('a3ddebfe-98aa-4c70-8c1e-02b96a1cacb9'))
