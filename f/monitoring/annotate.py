# SPDX-License-Identifier: copyleft-next-0.3.1
"""Post a suite run's window to Grafana as a region annotation (best-effort).

The window is the wall clock the wait steps reported: the earliest
`started_realtime_ms` to the latest `ended_realtime_ms` over the per-item
collect results (milliseconds since the epoch). The annotation is tagged
`suite-run` plus the suite, VM, kernel, and verdict, so the shipped
dashboard's annotations panel (filtering tag `suite-run`) shows test runs
over the metrics.

The Grafana endpoint and token live in the Windmill resource
`f/monitoring/grafana` (fields: `base_url` such as `http://127.0.0.1:3000`,
`token` a Grafana service-account token), created once by hand in the UI and
intentionally kept out of git (a sync would clobber it). The step fetches it
at runtime through the job's own Windmill API credentials; when the resource
is absent or blank, or no item carries a usable window, it logs why and
returns `{posted: False}` instead of failing the run. A configured but broken
Grafana does fail the step.

Equivalent command:

    curl --request POST --header "Authorization: Bearer $TOKEN" \
        --header "Content-Type: application/json" \
        --data '{"time": <started_ms>, "timeEnd": <ended_ms>,
                 "tags": ["suite-run", "<suite>", "<vm>", "<kernel>", "<verdict>"],
                 "text": "<suite> on <vm>: <verdict> (...)"}' \
        http://127.0.0.1:3000/api/annotations
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

RESOURCE_PATH = "f/monitoring/grafana"


def run_window(per_item: list) -> tuple[int | None, int | None]:
    """The run's wall-clock window: min started to max ended over the items."""
    starts = [
        it["started_realtime_ms"]
        for it in per_item
        if isinstance(it, dict) and it.get("started_realtime_ms") is not None
    ]
    ends = [
        it["ended_realtime_ms"]
        for it in per_item
        if isinstance(it, dict) and it.get("ended_realtime_ms") is not None
    ]
    if not starts or not ends:
        return None, None
    return int(min(starts)), int(max(ends))


def run_verdict(per_item: list) -> str:
    """`passed` only when every item is a collect dict with status `passed`."""
    ok = bool(per_item) and all(
        isinstance(it, dict) and it.get("status") == "passed" for it in per_item
    )
    return "passed" if ok else "failed"


def fetch_grafana_resource() -> dict | str:
    """The `f/monitoring/grafana` resource value, or a skip reason string."""
    base = os.environ.get("BASE_INTERNAL_URL", "")
    token = os.environ.get("WM_TOKEN", "")
    workspace = os.environ.get("WM_WORKSPACE", "")
    if not (base and token and workspace):
        return "Windmill job env incomplete (BASE_INTERNAL_URL/WM_TOKEN/WM_WORKSPACE)"
    url = (
        f"{base}/api/w/{workspace}/resources/get_value_interpolated/"
        f"{urllib.parse.quote(RESOURCE_PATH, safe='')}"
    )
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            value = json.load(resp)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return f"no resource at {RESOURCE_PATH}"
        raise
    if not isinstance(value, dict):
        return f"resource at {RESOURCE_PATH} is not an object"
    if not (value.get("base_url") and value.get("token")):
        return f"resource at {RESOURCE_PATH} lacks a base_url or token"
    return value


def main(
    suite: str = "",
    vm_name: str = "",
    kernel: str = "",
    per_item: list[dict] | None = None,
) -> dict:
    items = list(per_item or [])
    started, ended = run_window(items)
    if started is None or ended is None:
        print(
            f"no usable run window in {len(items)} item(s), skipping annotation",
            flush=True,
        )
        return {"posted": False, "reason": "no run window"}

    grafana = fetch_grafana_resource()
    if isinstance(grafana, str):
        print(
            f"no Grafana resource at {RESOURCE_PATH}, skipping annotation ({grafana})",
            flush=True,
        )
        return {"posted": False, "reason": grafana}

    verdict = run_verdict(items)
    body = {
        "time": started,
        "timeEnd": ended,
        "tags": [t for t in ("suite-run", suite, vm_name, kernel, verdict) if t],
        "text": f"{suite} on {vm_name}: {verdict} "
        f"({len(items)} item(s), kernel {kernel})",
    }
    url = f"{str(grafana['base_url']).rstrip('/')}/api/annotations"
    print(f"POST {url}", flush=True)
    print(json.dumps(body), flush=True)
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {grafana['token']}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        reply = json.load(resp)
    annotation_id = reply.get("id")
    print(f"posted annotation id={annotation_id}", flush=True)
    return {
        "posted": True,
        "id": annotation_id,
        "verdict": verdict,
        "time": started,
        "timeEnd": ended,
    }
