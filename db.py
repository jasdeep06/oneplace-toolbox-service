from __future__ import annotations
from psycopg2.extras import RealDictCursor


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
WHERE ms.id = %(server_id)s
ORDER BY ts.name, t.name, d.name;        
"""

from collections import OrderedDict, defaultdict
import json, re, yaml

from yaml.representer import SafeRepresenter
import yaml, collections

yaml.add_representer(
    collections.OrderedDict,
    SafeRepresenter.represent_dict,            # treat it like a normal dict
    Dumper=yaml.SafeDumper                     # register it for safe_dump
)


_slug_rx = re.compile(r"[^a-z0-9]+")
def _slug(text: str) -> str:
    return _slug_rx.sub("-", text.lower()).strip("-")

def make_yaml(rows: list[dict]) -> str:
    # ---------- 1. SOURCES ---------------------------------------------------
    src_by_conn: dict[str, str] = {}
    sources_od: OrderedDict[str, dict] = OrderedDict()

    for row in rows:
        conn_id = row["connection_id"]
        if conn_id in src_by_conn:
            continue

        cfg = row["connection_params"]
        key = _slug(row["connection_name"] or f"conn-{len(src_by_conn)+1}")
        src_by_conn[conn_id] = key

        sources_od[key] = OrderedDict(
            kind     = row["kind"],
            host     = cfg["host"],
            port     = int(cfg.get("port", 5432)),
            database = cfg["database"],
            user     = cfg["username"],
            password = cfg["password"],
        )

    # ---------- 1b. METADATA SOURCE (static) -------------------------------
    metadata_source = OrderedDict(
        host     = "ep-ancient-shape-a1kjibq3-pooler.ap-southeast-1.aws.neon.tech",
        port     = 5432,
        database = "neondb",
        user     = "neondb_owner",
        password = "npg_OqZYgaH46CQb",
    )

    # ---------- 2. TOOLS -----------------------------------------------------
    tools_od: OrderedDict[str, dict] = OrderedDict()
    ds_for_tool: defaultdict[str, set[str]] = defaultdict(set)

    for row in rows:
        tool_key = _slug(row["tool_name"])
        src_key  = src_by_conn[row["connection_id"]]
        kind     = f"{row['kind']}-sql" if not row["kind"].endswith("-sql") else row["kind"]

        # collect datasource ids
        if row["datasource_id"]:
            ds_for_tool[tool_key].add(row["datasource_id"])

        if tool_key in tools_od:
            continue

        try:
            params_json = row["tool_params"]
        except Exception:
            params_json = []

        tools_od[tool_key] = OrderedDict(
            kind       = kind,
            source     = src_key,
            description= row["tool_description"],
            parameters = params_json,
            statement  = row["sql_query"].rstrip() + "\n",
        )

    # attach comma-separated datasource IDs
    for tkey, ids in ds_for_tool.items():
        tools_od[tkey]["datasource_ids"] = ",".join(sorted(ids))

    # ---------- 3. TOOLSETS --------------------------------------------------
    toolsets_od: OrderedDict[str, list[str]] = OrderedDict()
    for row in rows:
        ts_key   = _slug(row["toolset_name"])
        tool_key = _slug(row["tool_name"])
        toolsets_od.setdefault(ts_key, []).append(tool_key)

    for ts_key, lst in toolsets_od.items():
        seen = set()
        toolsets_od[ts_key] = [x for x in lst if not (x in seen or seen.add(x))]

    # ---------- 4. Assemble YAML tree ---------------------------------------
    doc = OrderedDict(
        sources         = sources_od,
        metadata_source = metadata_source,
        tools           = tools_od,
        toolsets        = toolsets_od,
    )

    # write SQL in literal block style
    class _Lit(str): pass
    yaml.add_representer(
        _Lit,
        lambda dumper, data: dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|"),
        Dumper=yaml.SafeDumper
    )
    for t in tools_od.values():
        t["statement"] = _Lit(t["statement"])

    return yaml.safe_dump(doc, sort_keys=False, default_flow_style=False)

# Usage:
# rows = [dict(r) for r in session.exec(stmt).all()]
# print(make_yaml(rows))


#fire query using psycopg2
import psycopg2
import os

DATABASE_URL = 'postgresql://postgres:postgres@localhost:5433/oneplace_core'

def get_toolset_by_server_id(server_id: str):
    """Return a list of dicts, one per (toolset, tool, datasource) row."""
    with psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor) as conn:
        with conn.cursor() as cur:
            cur.execute(query, {'server_id': server_id})       # parameterized!
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
            cur.execute(port_query, {'server_id': server_id})
            results = cur.fetchone()
            return results["server_url"], results["port"]




# print(get_server_url_and_port('1dd10264-432d-411f-95f2-4b3cff101471'))