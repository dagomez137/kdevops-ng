#!/usr/bin/env python3
# SPDX-License-Identifier: copyleft-next-0.3.1
#
# Generate the `local_config` field default in f/fstests/check.flow/flow.yaml from
# the XFS catalog (f/fstests/common.py:xfs_catalog_text). The default is the full
# block/sector x feature matrix the run form shows in the editable local.config
# textarea; keeping it generated makes the catalog the single source so the shown
# matrix, the Sections picker, and render_config's fallback never drift. Re-run this
# after changing XFS_FEATURES / the geometry, and commit the regenerated flow.
#
#     python3 scripts/gen-fstests-config.py            # write the default
#     python3 scripts/gen-fstests-config.py --check    # drift guard (nix flake check)
import re
import sys

import yaml

from f.fstests.common import xfs_catalog_text

DEST = "f/fstests/check.flow/flow.yaml"
FIELD = "        local_config:"  # 8-space, the field key in check.properties
DEFAULT_KEY = "          default:"  # 10-space, the field's default


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _default_block(matrix: str) -> list[str]:
    """The `default: |` literal block, the matrix indented under it (12 spaces)."""
    body = ["            " + ln if ln else "" for ln in matrix.rstrip("\n").split("\n")]
    return [DEFAULT_KEY + " |", *body]


def regenerate(text: str, matrix: str) -> str:
    lines = text.split("\n")
    i = lines.index(FIELD)
    j = next(k for k in range(i + 1, len(lines)) if lines[k].startswith(DEFAULT_KEY))
    # The default's value runs until the next non-blank line at field level (<= 8
    # spaces); blank lines inside the literal block are skipped by the `strip()` test.
    end = next(
        k
        for k in range(j + 1, len(lines))
        if lines[k].strip() and _indent(lines[k]) <= 8
    )
    return "\n".join(lines[:j] + _default_block(matrix) + lines[end:])


def _sections(cfg: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for blk in re.split(r"\n\s*\n", cfg.strip()):
        m = re.match(r"\[([^\]]+)\]", blk.strip())
        if m:
            out[m.group(1)] = "\n".join(blk.strip().splitlines()[1:])
    return out


matrix = xfs_catalog_text()

if "--check" in sys.argv[1:]:
    committed = (
        yaml.safe_load(open(DEST))["schema"]["properties"]["check"]["properties"][
            "local_config"
        ].get("default")
        or ""
    )
    want, have = _sections(matrix), _sections(committed)
    if want == have:
        print(f"OK: {DEST} local.config default is up to date ({len(want)} sections)")
        sys.exit(0)
    only_want = sorted(set(want) - set(have))
    only_have = sorted(set(have) - set(want))
    body = [k for k in want.keys() & have.keys() if want[k] != have[k]]
    sys.stderr.write(
        f"{DEST} local.config default drifted from xfs_catalog_text():\n"
        f"  missing (in catalog, not committed): {only_want[:8]} ({len(only_want)})\n"
        f"  stale (committed, not in catalog): {only_have[:8]} ({len(only_have)})\n"
        f"  body mismatch: {body[:8]} ({len(body)})\n"
        "Run `python3 scripts/gen-fstests-config.py`.\n"
    )
    sys.exit(1)

with open(DEST) as fh:
    out = regenerate(fh.read(), matrix)
with open(DEST, "w") as fh:
    fh.write(out)
print(f"wrote {DEST}: {len(_sections(matrix))} sections in the local.config default")
