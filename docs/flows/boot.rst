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
   backing file of each emulated NVMe drive that has one. A drive on a
   block driver has no file, and the shipped drive is one of those.
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
  nvme``/``nvme-ns``, a block driver or an image file each);
  ``discard`` and ``detect_zeroes`` govern how much host disk an image
  holds, and the testing-oriented knobs are covered by
  :doc:`nvme-testing`.
- **Orchestration** bounds the flow-level boot wait and the debug
  snapshot.

What the shipped drive is
=========================

``driver`` decides what a drive sits on, and it ships as ``null-co``:
the request is answered in the QEMU block layer and never reaches
storage. A sweep of the block modes (backing driver, image format,
``aio`` mode, cache mode) put that one on top, at roughly twice the
request rate of the qcow2 image this form used to ship. Nothing is
stored, a read comes back zeroed, and a guest that has to hold a
filesystem needs ``driver`` cleared on the drives that do. Clearing it
gives an image file, and the rest of this page is about those.

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

Where the doorbell write lands
==============================

A guest tells an emulated NVMe controller that it has queued a command
by writing that queue's doorbell register, and where the write lands
decides most of the device's throughput. By default QEMU traps it: the
write faults out of the guest, KVM hands it to QEMU userspace on the
vCPU thread, and that thread runs no guest code again until the
controller has taken the command. The ``ioeventfd`` knob moves the write
onto an eventfd instead, so KVM signals the main loop and the vCPU
thread never leaves the guest.

QEMU leaves ``ioeventfd`` off and this form turns it on for every drive,
because nothing measured here prefers the trap. A pair of boots that
differ in the doorbell alone, same image, same ``aio`` and cache mode,
put the eventfd at roughly double the request rate at queue depth one,
with the gain shrinking as the commands grow and gone by 1 MiB: the exit
is a fixed cost per command, not per byte. The admin queue keeps the
trap either way, and ``hw/nvme/ctrl.c`` has no ``iothread`` property, so
the I/O still runs in the main loop: what the eventfd removes is the
vCPU exit, not the emulation.

Turn it off to reproduce a measurement taken before this default
changed. A number captured under the trap does not compare with one
captured without it.

One boot, several block modes
=============================

Every drive knob takes either one value for all the drives or a
comma-list that assigns by drive index, so one guest can carry several
block configurations at once and a measurement can compare them without
a reboot in between. An empty part takes the knob's own default, except
on ``driver``, where an empty part is how a drive asks for an image.

``driver`` carries the null block driver on every drive by default
(``null-co``, or ``null-aio`` to answer from the AIO path instead).
Clearing it on a drive gives that drive an image file, and the image
knobs follow: ``format`` picks the format and names the backing file
after it, ``aio`` picks how the host submits its I/O (a thread pool,
Linux AIO, or io_uring), and ``cache`` picks the host cache mode, where
``none`` and ``directsync`` open the file ``O_DIRECT``.

Linux AIO only works on an ``O_DIRECT`` file. QEMU refuses that
combination when it opens the image, which is halfway through boot, so
the render refuses it first.

Five drives, the first keeping the shipped null driver and the other
four on images, each changing one thing:

.. code-block:: json

   "boot_nvme": {"nvme_drive_count": 5, "customize_drives": true,
                 "driver": "null-co,,,,",
                 "format": ",qcow2,qcow2,raw,raw",
                 "aio": ",threads,io_uring,io_uring,native",
                 "cache": ",writeback,writeback,writeback,none"}

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
