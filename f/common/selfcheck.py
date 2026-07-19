# SPDX-License-Identifier: copyleft-next-0.3.1
"""Deploy-time contracts for the suite flows (library, not a runnable step).

Imported with:  from f.common import selfcheck

Windmill's CI-test mechanism runs each suite's `selfcheck` step whenever an
annotated flow or script of that suite deploys. The step calls `check`, which
drives the DEPLOYED collect, report, and judge scripts with the same fixture
and degrade arguments as `nix run .#preview-smoke` and asserts the contracts
the flows lean on: an empty run is failed, `report` returns `render_all` as
the sole key, `judge` fails a red run and passes a green report through
unchanged, and a crashed run can never read as a pass. The report cases omit
`vm_name`, which skips the rollup write, and the collect case names a VM that
cannot exist, so no guest and no share is touched.

Equivalent command, per case:

    wmill script run f/<suite>/report -d '{"per_<key>": []}'
"""

import json

import wmill

# The judge pass-through sentinel: on a green run judge must return the report
# it was handed, byte for byte.
GREEN_REPORT = {"render_all": [{"markdown": "selfcheck sentinel"}]}


def main():
    """This module is a library imported by the selfcheck steps, not a runnable step."""
    return "f/common/selfcheck: deploy-time contracts for the suite flows"


def _run(path: str, args: dict):
    print(f"+ run_script_by_path {path} {json.dumps(args)}", flush=True)
    return wmill.run_script_by_path(path, args)


def check(suite: str, per_key: str, item_arg: str) -> dict:
    base = f"f/{suite}"
    passing = {"status": "passed", item_arg: "smoke"}
    failing = {"status": "failed", "failed": 1, item_arg: "smoke"}
    hard_error = {"error": {"name": "SSH", "message": "poll failed"}}

    report = _run(f"{base}/report", {per_key: []})
    assert list(report) == ["render_all"] and report["render_all"], report
    report = _run(f"{base}/report", {per_key: [passing, failing, hard_error]})
    assert list(report) == ["render_all"] and report["render_all"], report

    red = None
    try:
        red = _run(f"{base}/judge", {per_key: [failing]})
    except Exception as exc:
        print(f"judge failed the red run as it must: {exc}", flush=True)
    assert red is None, f"judge passed a red run: {red!r}"

    green = _run(f"{base}/judge", {per_key: [passing], "report": GREEN_REPORT})
    assert green == GREEN_REPORT, green

    degraded = _run(
        f"{base}/collect",
        {
            "vm_name": "selfcheck",
            item_arg: "smoke",
            "kernel_version": "0.0.0-selfcheck",
            "crashed": True,
        },
    )
    assert degraded.get("status") == "failed", degraded

    return {"suite": suite, "cases": 5, "status": "passed"}
