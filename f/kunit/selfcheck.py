# test:
# flow/f/kunit/run
# script/f/kunit/*
# SPDX-License-Identifier: copyleft-next-0.3.1
"""Deploy-time selfcheck for the KUnit run flow (a Windmill CI test).

The `# test:` annotation makes Windmill run this script whenever the run
flow or any `f/kunit` script deploys; it drives the deployed collect,
report, and judge with fixture and degrade arguments and asserts the
contracts they share (see `f.common.selfcheck`), so a deploy that breaks a
verdict or render contract turns red on the spot.
"""

from f.common import selfcheck


def main() -> dict:
    return selfcheck.check("kunit", "per_suite", "suite")
