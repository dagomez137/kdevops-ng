# SPDX-License-Identifier: copyleft-next-0.3.1
"""Advance one kernel git-bisect iteration and name the next commit to test.

The whole bisect brain, called at the top of every `f/kernel/bisect` loop
iteration. State lives on disk under `$SYSTEM_DIR/bisect/<vm_name>/`: a
`--shared --no-checkout` clone of the Bare (objects borrowed, no tree
materialized; `git bisect --no-checkout` keeps the candidate in
`BISECT_HEAD`) plus `state.json`. The previous candidate's verdict comes
from disk too, never from flow results: the freshest `report.json` the
payload wrote since the last decision (the kunit or selftests run's report
on the VM's share, or `f/kernel/check_usertests`'s report in the state
dir). A failed bringup or run therefore cannot skip the decision; it just
reads as `skip`. The report contract is `passed` -> good, `untestable` ->
skip, anything else -> bad; with `max_runtime` set, a passed report whose
summed item runtime exceeds it reads as bad too, which is what a runtime
regression hunt bisects on.

Phases: `verify_bad` tests the bad ref standalone (a pass ends the run as
`not_reproducible_standalone`, itself a finding for ordering-dependent
failures); `verify_good` tests the good ref (a failure ends as
`good_endpoint_failed`); then `bisect` feeds `git bisect good|bad|skip`
until git names the first bad commit. A kernel that fails to build or boot
reads as `skip`, so this flow bisects suite verdicts, not boot failures.
Changing the good/bad/suites inputs, or rerunning after a concluded run,
resets the state and starts over.

Equivalent commands, in the state clone:

    git clone --shared --no-checkout "$SYSTEM_DIR/bare/linux.git" repo
    git bisect start --no-checkout <bad> <good>
    git bisect good|bad|skip <sha>
    git rev-parse --verify BISECT_HEAD
"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

from f.common.devshell import Git
from f.kunit.common import share_dir as kunit_share_dir
from f.selftests.common import share_dir as selftests_share_dir

_FIRST_BAD = "is the first bad commit"


def _state_dir(vm_name: str) -> Path:
    root = Path(os.environ["SYSTEM_DIR"]) / "bisect"
    path = (root / vm_name).resolve()
    if root.resolve() not in path.parents:
        raise ValueError(f"vm_name {vm_name!r} resolves outside {root}")
    return path


def _resolve(git: Git, repo: Path, ref: str) -> str:
    for candidate in (ref, f"origin/{ref}"):
        sha = git.capture(
            "-C",
            str(repo),
            "rev-parse",
            "--verify",
            f"{candidate}^{{commit}}",
            check=False,
        ).strip()
        if sha:
            return sha
    raise ValueError(f"ref {ref!r} does not resolve in the Bare clone at {repo}")


def _report_candidates(payload: str, vm_name: str, sdir: Path) -> list[Path]:
    if payload == "usertests_build":
        return [sdir / "report.json"]
    root = (selftests_share_dir if payload == "selftests" else kunit_share_dir)(vm_name)
    return [root / "report.json", *root.glob("*/report.json")]


def _fresh_verdict(
    candidates: list[Path], decided_at: float, max_runtime: float = 0.0
) -> str | None:
    """`good`/`bad`/`skip` from the newest report.json written after the last
    decision, None when no run reported (the payload never got there)."""
    best = None
    for path in candidates:
        if path.is_file() and (
            best is None or path.stat().st_mtime > best.stat().st_mtime
        ):
            best = path
    if best is None or best.stat().st_mtime <= decided_at:
        return None
    try:
        report = json.loads(best.read_text())
        status = report.get("status")
    except Exception:
        return None
    print(f"verdict source: {best}", flush=True)
    if status == "untestable":
        return "skip"
    if status != "passed":
        return "bad"
    if max_runtime:
        total = sum((i.get("runtime") or 0) for i in report.get("items") or [])
        print(
            f"runtime: total={round(total, 2)}s max_runtime={max_runtime}s", flush=True
        )
        if total > max_runtime:
            return "bad"
    return "good"


def _bisect_feed(git: Git, repo: Path, verdict: str, sha: str) -> str:
    out = git.capture("-C", str(repo), "bisect", verdict, sha, check=False)
    if out.strip():
        print(out.strip(), flush=True)
    return out


def _bisect_head(git: Git, repo: Path) -> str:
    return git.capture(
        "-C", str(repo), "rev-parse", "--verify", "BISECT_HEAD", check=False
    ).strip()


def main(
    vm_name: str,
    good: str,
    bad: str,
    suites: list[str] | None = None,
    max_steps: int = 20,
    payload: str = "kunit",
    error_re: str = "",
    max_runtime: float = 0.0,
) -> dict:
    suites = list(suites or [])
    if not suites:
        raise ValueError("suites must name at least one suite to bisect on")
    if payload not in ("kunit", "usertests_build", "selftests"):
        raise ValueError(f"unknown payload {payload!r}")
    if payload == "usertests_build":
        from f.kernel.build_usertests import CATALOG

        unknown = [s for s in suites if s not in CATALOG]
        if unknown:
            raise ValueError(
                f"unknown usertests harness(es): {' '.join(unknown)} "
                f"(known: {' '.join(CATALOG)})"
            )
    sdir = _state_dir(vm_name)
    state_file = sdir / "state.json"
    repo = sdir / "repo"
    workers = Path(os.environ["WORKERS_DIR"])
    git = Git(workers)

    state: dict = {}
    if state_file.is_file():
        try:
            state = json.loads(state_file.read_text())
        except Exception:
            state = {}
    stale = (
        not state
        or state.get("good") != good
        or state.get("bad") != bad
        or state.get("suites") != suites
        or state.get("payload", "kunit") != payload
        or state.get("error_re", "") != error_re
        or state.get("max_runtime", 0.0) != max_runtime
        or state.get("outcome")
    )

    if stale:
        if sdir.exists():
            shutil.rmtree(sdir)
            print(f"reset {sdir}", flush=True)
        sdir.mkdir(parents=True)
        bare = Path(os.environ["SYSTEM_DIR"]) / "bare/linux.git"
        git.run("clone", "--shared", "--no-checkout", str(bare), str(repo))
        bad_sha = _resolve(git, repo, bad)
        # The Bare's default branch may not exist in the clone, leaving HEAD
        # unborn, which `git bisect start` rejects; pin it to the bad commit.
        git.run("-C", str(repo), "update-ref", "refs/heads/bisect-base", bad_sha)
        git.run("-C", str(repo), "symbolic-ref", "HEAD", "refs/heads/bisect-base")
        state = {
            "good": good,
            "bad": bad,
            "suites": suites,
            "payload": payload,
            "error_re": error_re,
            "max_runtime": max_runtime,
            "good_sha": _resolve(git, repo, good),
            "bad_sha": bad_sha,
            "phase": "verify_bad",
            "candidate": "",
            "decided_at": 0.0,
            "steps": 0,
            "iterations": [],
            "outcome": "",
        }
        state["candidate"] = state["bad_sha"]
    else:
        candidates = _report_candidates(payload, vm_name, sdir)
        verdict = (
            _fresh_verdict(candidates, float(state["decided_at"]), max_runtime)
            or "skip"
        )
        prev = state["candidate"]
        phase = state["phase"]
        print(f"verdict={verdict} candidate={prev} phase={phase}", flush=True)
        if phase == "verify_bad":
            if verdict == "good":
                state["outcome"] = "not_reproducible_standalone"
            elif verdict == "bad":
                state["phase"] = "verify_good"
                state["candidate"] = state["good_sha"]
            else:
                state["outcome"] = "endpoint_untestable"
        elif phase == "verify_good":
            if verdict == "good":
                git.run(
                    "-C",
                    str(repo),
                    "bisect",
                    "start",
                    "--no-checkout",
                    state["bad_sha"],
                    state["good_sha"],
                )
                head = _bisect_head(git, repo)
                if head:
                    state["phase"] = "bisect"
                    state["candidate"] = head
                else:
                    state["outcome"] = "inconclusive"
            elif verdict == "bad":
                state["outcome"] = "good_endpoint_failed"
            else:
                state["outcome"] = "endpoint_untestable"
        elif phase == "bisect":
            out = _bisect_feed(git, repo, verdict, prev)
            state["steps"] = int(state["steps"]) + 1
            if _FIRST_BAD in out:
                state["outcome"] = "first_bad_found"
                state["first_bad"] = out.split()[0]
            else:
                head = _bisect_head(git, repo)
                if not head:
                    state["outcome"] = "inconclusive"
                elif int(state["steps"]) >= int(max_steps):
                    state["outcome"] = "max_steps_exceeded"
                else:
                    state["candidate"] = head
        state["iterations"].append(
            {"phase": phase, "candidate": prev, "verdict": verdict}
        )

    state["decided_at"] = time.time()
    state_file.write_text(json.dumps(state, indent=2) + "\n")
    print(f"wrote {state_file}", flush=True)
    done = bool(state.get("outcome"))
    result = {
        "done": done,
        "phase": state["phase"],
        "candidate": state["candidate"],
        "outcome": state.get("outcome", ""),
        "steps": state["steps"],
    }
    if done:
        print(f"outcome={state['outcome']}", flush=True)
    else:
        print(
            f"next: phase={state['phase']} candidate={state['candidate']}", flush=True
        )
    return result
