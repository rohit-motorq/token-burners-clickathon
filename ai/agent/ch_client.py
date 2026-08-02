"""Tiny ClickHouse HTTP client, stdlib only. Every query is parameter-bound —
this module is the only thing allowed to touch ClickHouse; tools call it,
the LLM never sees a raw SQL string. Mirrors Base/SonyLiv/evals/ch_client.py."""
import base64
import json
import urllib.parse
import urllib.request

from . import config


def query(sql: str, parameters: dict | None = None, fmt: str = "JSONEachRow") -> list[dict]:
    # ClickHouse HTTP interface binds {name:Type} placeholders in the SQL body
    # to param_<name>=<value> query-string args — this is the parameter
    # binding boundary; tools pass user input here, never string-interpolate SQL.
    body = (sql.strip() + f"\nFORMAT {fmt}").encode()
    url = config.CH_URL
    qs_params = {"database": config.CH_DATABASE}
    if parameters:
        qs_params.update({f"param_{k}": v for k, v in parameters.items()})
    url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(qs_params)
    req = urllib.request.Request(url, data=body, method="POST")
    auth = f"{config.CH_USER}:{config.CH_PASS}".encode()
    req.add_header("Authorization", "Basic " + base64.b64encode(auth).decode())
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read().decode()
    if fmt == "JSONEachRow":
        return [json.loads(line) for line in raw.splitlines() if line]
    return raw


def scalar(sql: str, parameters: dict | None = None):
    rows = query(sql, parameters)
    if not rows:
        return None
    return list(rows[0].values())[0]
