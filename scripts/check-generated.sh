#!/usr/bin/env bash
# SPDX-License-Identifier: copyleft-next-0.3.1
# Drift guard for in-repo generated files. Each generator owns a --check mode that
# regenerates in memory and diffs against the committed output, exiting non-zero on
# drift. The reflow guard instead fails if any committed wmill description would
# fold (a line past 80 columns); `nix run .#reflow` fixes it. This is the flake's
# `generated` check, so `nix flake check` runs it.
set -o errexit -o nounset -o pipefail

python3 scripts/gen-bringup.py --check
# gen-fstests-config imports f.fstests.common (the XFS catalog); the others only
# read files, so only this one needs the repo root on the import path.
PYTHONPATH="$PWD" python3 scripts/gen-fstests-config.py --check
python3 scripts/reflow-descriptions.py --check
