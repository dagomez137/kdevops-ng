# SPDX-License-Identifier: copyleft-next-0.3.1
"""Run the kernel's Coccinelle semantic patches in report mode.

Documentation/dev-tools/coccinelle.rst: "The value 2 is passed to the C
variable to check files regardless of whether they need to be compiled or
not", as `make C=2 CHECK=scripts/coccicheck <object>`. `scope=series` does
that for the series' own objects; `scope=worktree` runs `make coccicheck`
over every file, which takes hours. `MODE=report` prints one
`file:line:col: WARNING:` line per match. Coccinelle's matches carry false
positives, so they are counted, never failed on.

Each rule under `scripts/coccinelle/` runs on its own (`COCCI=<rule>`), with
its own log: `coccicheck` stops at the first rule `spatch` cannot parse, and a kernel
can carry one written for a newer Coccinelle than the devShell's (v7.3's
`hid/ff_race.cocci` puts a position on a disjunction, which 1.3.1 rejects).
Such a rule is listed in `rules_failed` and the rest still report; a rule with
no report mode is listed in `rules_skipped`.

`spatch` finds libpython through `pkg-config --libs python3`, which in the
devShell prints no `-l` and stops it with "unable to parse the output of
pkg-config"; with `PKG_CONFIG_PATH` unset it falls back to a probe that works,
so `make` runs under `env --unset=PKG_CONFIG_PATH`.

Equivalent bash, run inside the nixos-flake check-kernel devShell:

    env --unset=PKG_CONFIG_PATH \\
        make --directory="$worktree" O="$build_dir" $make_flags MODE=report \\
        COCCI="$worktree/scripts/coccinelle/free/kfree.cocci" \\
        C=2 CHECK="$worktree/scripts/coccicheck" lib/xarray.o \\
        | tee "$check_dir/coccicheck/free/kfree.log"        # scope=series
    env --unset=PKG_CONFIG_PATH \\
        make --directory="$worktree" O="$build_dir" $make_flags MODE=report \\
        COCCI="$worktree/scripts/coccinelle/free/kfree.cocci" coccicheck
"""

from __future__ import annotations

import re
from pathlib import Path

from f.kernel.checks import (
    check_dir,
    diagnostics,
    in_files,
    kernel_objects,
    make,
    result,
    shell,
)

# `coccicheck`'s own test for a rule that has a report mode.
_REPORT = re.compile(r"virtual\s+report")


def main(
    worktree: str,
    build_dir: str,
    files: list[str],
    scope: str = "series",
    make_flags: str = "",
) -> dict:
    if scope == "series":
        objects = kernel_objects(files, build_dir)
        if not objects:
            print("the series changes no C file the Build compiles", flush=True)
            return result("coccicheck", [], None, scope=scope, objects=[])
        goals = ["C=2", f"CHECK={worktree}/scripts/coccicheck", *objects]
    else:
        objects, goals = [], ["coccicheck"]
    rules = sorted((Path(worktree) / "scripts/coccinelle").rglob("*.cocci"))
    findings, failed_rules, skipped_rules = [], [], []
    sh = shell()
    for rule in rules:
        name = str(rule.relative_to(Path(worktree) / "scripts/coccinelle"))
        if not _REPORT.search(rule.read_text()):
            skipped_rules.append(name)
            continue
        log = check_dir() / "coccicheck" / name.replace(".cocci", ".log")
        rc = sh.tee(
            "env",
            "--unset=PKG_CONFIG_PATH",
            *make(
                worktree, build_dir, make_flags, "MODE=report", f"COCCI={rule}", *goals
            ),
            log=log,
        )
        text = log.read_text()
        if rc:
            failed_rules.append(name)
        findings += [{**f, "level": "warning"} for f in diagnostics(text, worktree)]
    if scope == "series":
        findings = in_files(findings, files)
    print(
        f"{len(rules)} rules, {len(skipped_rules)} without a report mode, "
        f"{len(failed_rules)} `spatch` could not run",
        flush=True,
    )
    return result(
        "coccicheck",
        findings,
        check_dir() / "coccicheck",
        scope=scope,
        objects=objects,
        rules=len(rules),
        rules_failed=failed_rules,
        rules_skipped=skipped_rules,
        failed=bool(rules) and len(failed_rules) == len(rules),
    )
