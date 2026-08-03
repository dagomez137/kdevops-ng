# test:
# flow/f/blktests/check
# script/f/blktests/*
# SPDX-License-Identifier: copyleft-next-0.3.1
"""Deploy-time selfcheck for the blktests check flow (a Windmill CI test).

The `# test:` annotation makes Windmill run this script whenever the check
flow or any `f/blktests` script deploys; it drives the deployed collect,
report, and judge with fixture and degrade arguments and asserts the
contracts they share (see `f.common.selfcheck`), so a deploy that breaks a
verdict or render contract turns red on the spot.
"""

from f.common import selfcheck


def main() -> dict:
    return selfcheck.check("blktests", "per_group", "group")
