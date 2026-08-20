.. SPDX-License-Identifier: copyleft-next-0.3.1

:orphan:

=========
Boot a VM
=========

The :src:`f/qsu/boot` flow is the orchestrate half of guest bringup: it
pairs a kernel from :doc:`kernel-build` with an imageless NixOS closure
from :doc:`nix-build` and turns them into a running guest, managed as a
per-user ``qemu-system@<vm>.service`` systemd unit and registered with
:cmd:`systemd-machined`. Everything the emulator needs is rendered as
unit files, environment files, and drop-ins, so the running VM is
host-native systemd state: it survives Windmill, restarts with
:cmd:`systemctl`, and appears in :cmd:`machinectl` like any other
machine. :doc:`bringup` embeds this flow as its boot tail; run it
directly when the artifacts already exist.

The flow is thin:

1. ``render_qemu_system``: render the ``qemu-system@.service`` template
   (once), the per-VM ``<vm>.env`` variable file, the
   ``qemu-system@<vm>.service.d/override.conf`` drop-in, and the QMP
   powerdown helper.
2. ``render_virtiofsd``: render the ``virtiofsd@.service`` and
   ``virtiofsd@.socket`` pair (once) plus a per-share environment file
   and drop-in for each composed share, wired from the shares the first
   step composed.
3. ``create_nvme``: :cmd:`qemu-img` ``create --format qcow2`` the per-VM
   backing file of each emulated NVMe drive.
4. ``boot``: ``daemon-reload``, restart the share sockets, start
   ``qemu-system@<vm>``, and wait for the guest's sshd banner. Its
   access manifest is the flow result.

Every step runs on the dedicated ``vm`` worker, the only one that mounts
the host's ``~/.config/systemd`` and ``~/.local/state/qemu-system`` the
renders write into. The kernel and closure may be built on any worker:
the host-forked unit reads them from ``/nix/store`` and the shared build
area regardless.

The run form
============

Form fields are the upstream QEMU flag names (the rendered units'
variable surface), grouped:

- **VM** names the guest and how its identity is assigned.
- **QEMU** picks the emulator source and the machine model (``cpu``,
  ``accel``, ``m``, ``smp``, ``machine_type``).
- **Kernel boot** takes the kernel build manifest for direct kernel
  boot (``-kernel``/``-initrd``/``-append``) and wires the
  ``/lib/modules`` share to the built modules.
- **Networking** sets the host vsock and SSH forwarding: a base plus a
  per-VM offset (``vm_index``, or derived).
- **File sharing** composes the virtiofs shares (modules, controller)
  and picks the :cmd:`virtiofsd` binary.
- **NVMe** declares the emulated NVMe drives (``-device
  nvme``/``nvme-ns``, one qcow2 per drive); ``discard`` and
  ``detect_zeroes`` govern how much host disk they hold, and the
  testing-oriented knobs are covered by :doc:`nvme-testing`.
- **Orchestration** bounds the flow-level boot wait and the debug
  snapshot.

Keeping the drive images thin
=============================

``create_nvme`` creates each backing file at its full virtual size, but
qcow2 only allocates host blocks on write, so a fresh drive costs about
200 KiB no matter how large it claims to be. What makes an image grow is
the guest touching new blocks, and what keeps it from growing forever is
the guest's deallocations reaching the host.

The ``discard`` knob decides whether they do. It defaults to ``unmap``,
which passes NVMe DSM deallocate and TRIM through to the image, so
:cmd:`fstrim`, a discarding ``mkfs``, :cmd:`blkdiscard`, and file
deletion punch the freed clusters back out of the qcow2. With ``off``
QEMU accepts those commands and silently drops them, so every cluster a
test ever touched stays allocated and each drive climbs to its virtual
size and stays there, however little the guest still stores. That is
worth knowing when a run leaves several idle VMs behind: a test rig with
five 20 GiB drives per VM pins 100 GiB per guest once the images are
full.

The related ``detect_zeroes`` knob converts all-zero guest writes into
deallocations rather than data, and is off by default. QEMU refuses to
open an image with ``detect-zeroes=unmap`` unless ``discard`` is also
``unmap``, so the render fails that combination early rather than
letting the VM die in boot. Leave it off for write benchmarks, where it
would retire a zero-fill as a discard and flatter the result.

Both knobs take a single value or a per-drive comma-list, so one drive
can keep discards while the rest drop them. Reclaiming an image that
already grew needs the guest to discard what it no longer uses
(:cmd:`fstrim` on a mounted filesystem), or, with the VM stopped,
deleting the backing file and letting ``create_nvme`` lay a fresh one on
the next boot.

Watching and driving the VM
===========================

The job log is the primary view while the flow runs (:doc:`guests`); the
``boot`` step's result carries the access manifest, and after that the
VM is ordinary systemd state. The equivalent manual workflow, once the
units are rendered, against the host ``systemd --user`` manager:

.. code-block:: console
   :caption: host
   :class: cmd-host

   $ systemctl --user daemon-reload
   $ systemctl --user start qemu-system@<vm>
   $ systemctl --user list-units 'qemu-system@*'
   $ journalctl --user-unit=qemu-system@<vm>.service
   $ systemctl --user stop qemu-system@<vm>

Booting is re-entrant: the ``boot`` step restarts a deployed VM with
the current render, which is how a VM is reconfigured in place
(:doc:`bringup`'s refresh target rides exactly this). :doc:`guests`
covers reaching the booted guest (SSH, ``--host``, ``machinectl``)
and stopping or killing it.
