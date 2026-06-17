#!/usr/bin/env bash
# SPDX-License-Identifier: copyleft-next-0.3.1
# Drift guard for in-repo generated files. Each generator owns a --check mode that
# regenerates in memory and diffs against the committed output, exiting non-zero on
# drift. Invoked by `make generated` (and `make style`).
set -o errexit -o nounset -o pipefail

python3 scripts/gen-bringup.py --check
