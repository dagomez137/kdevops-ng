# SPDX-License-Identifier: copyleft-next-0.3.1
"""List who to send the series to, from the MAINTAINERS file.

Documentation/process/submitting-patches.rst: "scripts/get_maintainer.pl can
be very useful at this step (pass paths to your patches as arguments to
scripts/get_maintainer.pl)". It runs from the Worktree root over the patch files,
the cover letter left out since it carries no diff. The list is kept as
`maintainers.txt` in `check/`, in the form `git send-email --to` and
`--cc` take.

Equivalent bash, run inside the nixos-flake check-kernel devShell, from the
Worktree root:

    scripts/get_maintainer.pl "$check_dir"/patches/0*.patch
"""

from __future__ import annotations

from f.kernel.checks import check_dir, result, shell


def main(worktree: str, patches: list[str]) -> dict:
    out = shell().capture("scripts/get_maintainer.pl", *patches, cwd=worktree)
    entries = [line for line in out.splitlines() if line.strip()]
    listing = check_dir() / "maintainers.txt"
    listing.write_text("".join(f"{e}\n" for e in entries))
    print(out, end="", flush=True)
    print(f"wrote {listing} ({len(entries)} entries)", flush=True)
    return result("get_maintainer", [], listing, file=str(listing), entries=entries)
