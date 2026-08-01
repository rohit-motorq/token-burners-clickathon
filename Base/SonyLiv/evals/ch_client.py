"""Tiny ClickHouse HTTP client. stdlib only, no clickhouse-driver dep."""
import json
import os
import time
import urllib.parse
import urllib.request
import uuid

CH_URL = os.environ.get("CH_URL", "https://mg6ws6jmpr.ap-south-1.aws.clickhouse.cloud:8443")
CH_USER = os.environ.get("CH_USER", "default")
CH_PASS = os.environ.get("CH_PASS", "DApBb4.O_9tqI")


def query(sql, fmt="JSONEachRow", query_id=None):
    url = CH_URL
    if query_id:
        url = url + "?" + urllib.parse.urlencode({"query_id": query_id})
    body = (sql.strip() + f"\nFORMAT {fmt}").encode()
    req = urllib.request.Request(url, data=body, method="POST")
    auth = f"{CH_USER}:{CH_PASS}".encode()
    import base64
    req.add_header("Authorization", "Basic " + base64.b64encode(auth).decode())
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read().decode()
    if fmt == "JSONEachRow":
        return [json.loads(line) for line in raw.splitlines() if line]
    return raw


def _exec_raw(sql):
    """For statements that reject a FORMAT clause (e.g. SYSTEM FLUSH LOGS)."""
    req = urllib.request.Request(CH_URL, data=sql.strip().encode(), method="POST")
    auth = f"{CH_USER}:{CH_PASS}".encode()
    import base64
    req.add_header("Authorization", "Basic " + base64.b64encode(auth).decode())
    with urllib.request.urlopen(req, timeout=60) as resp:
        resp.read()


def query_with_stats(sql):
    """Runs sql with a known query_id, flushes logs, and pulls its own
    read_rows/read_bytes/query_duration_ms back out of system.query_log —
    the only way to see what a query actually read, not just how long it took.

    ClickHouse Cloud splits compute across nodes (compute-compute separation),
    so the query and our later log lookup can land on different replicas —
    a plain `system.query_log` read misses it. Cluster-wide flush + read via
    clusterAllReplicas() is what actually finds it."""
    qid = str(uuid.uuid4())
    t0 = time.time()
    rows = query(sql, query_id=qid)
    wall_ms = (time.time() - t0) * 1000
    _exec_raw("SYSTEM FLUSH LOGS ON CLUSTER default")
    stat_rows = query(f"""
        SELECT read_rows, read_bytes, query_duration_ms
        FROM clusterAllReplicas(default, system.query_log)
        WHERE query_id = '{qid}' AND type = 'QueryFinish'
        LIMIT 1
    """)
    stats = stat_rows[0] if stat_rows else {}
    stats["wall_ms"] = wall_ms
    return rows, stats


def scalar(sql):
    rows = query(sql)
    if not rows:
        return None
    return list(rows[0].values())[0]


def table_exists(name):
    rows = query(f"EXISTS TABLE {name}")
    return bool(rows) and int(list(rows[0].values())[0]) == 1


def table_ready(name):
    """Exists AND has rows — a table can be DDL'd but not yet ingesting."""
    if not table_exists(name):
        return False
    return (scalar(f"SELECT count() FROM {name}") or 0) > 0
