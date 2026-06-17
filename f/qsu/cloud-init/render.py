# SPDX-License-Identifier: copyleft-next-0.3.1
"""DEFERRED scaffold: render the cloud-init seed component.

Will render the cloud-init templates — `user-data.j2` and `meta-data.j2` (plus the
`files/network-config` seed) — into a NoCloud seed image that `vm.env.j2` attaches as
the `seed` drive, for image-backed guests that provision over cloud-init rather than
the imageless closure. Body intentionally unimplemented; see the plan's Deferred
section.
"""

from __future__ import annotations


def main(vm_name: str = "") -> dict:
    return {"deferred": True}
