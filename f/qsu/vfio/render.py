# SPDX-License-Identifier: copyleft-next-0.3.1
"""DEFERRED scaffold: render the vfio (PCI passthrough) component.

Will render the VFIO templates (`vfio-bind@.service.j2`, which binds a host PCI device
to vfio-pci before VM start, via the per-VM drop-in's `Requires=vfio-bind@<addr>`, and
`vfio-udev.rules.j2`) plus the `files/vfio-pci.conf` modprobe drop. Body intentionally
unimplemented until the MVP imageless boot path lands. See the plan's Deferred section.
"""

from __future__ import annotations


def main(vm_name: str = "") -> dict:
    return {"deferred": True}
