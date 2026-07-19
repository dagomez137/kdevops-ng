.. SPDX-License-Identifier: copyleft-next-0.3.1

:orphan:

=============
Bring up a VM
=============

The :src:`f/qsu/bringup` flow is the one-shot path from sources to a
running guest: build and/or reuse each artifact (a kernel, an imageless
NixOS closure, QEMU), then boot a VM from them. It composes the build
flows (:doc:`kernel-build`, :doc:`nix-build`, :doc:`qemu-build`) and
:doc:`boot` behind one form, so the zero-config run already produces a
useful guest.

Artifact source and deploy target are two orthogonal axes. Each
artifact component has a mode:

- **Kernel**: ``build`` runs the kernel build subflow; ``reuse`` picks
  a published ``kernel-<release>`` from this host's store index
  (:doc:`/concepts/build-store`).
- **NixOS closure**: ``build`` renders and builds the closure from the
  curated profile and test-suite picks; ``reuse`` replays the init and
  initrd a previously deployed VM recorded. The closure is not
  store-indexed on purpose: the Nix store content-addresses it, so an
  unchanged configuration rebuilds to the same result and the build
  mode auto-skips.
- **QEMU**: ``build`` runs the QEMU build subflow, ``reuse`` picks a
  published ``qemu-<identity>`` from the store index, and ``nixpkgs``
  boots the stock nixpkgs QEMU straight from the store.

The final **VM** group is the deploy target: a fresh VM, or refresh a
deployed one in place (the boot tail restarts the unit with the new
render). Picking a published store kernel and refreshing an existing VM
with it is the supported way to swap a guest's kernel.

The flow chains ``resolve`` then the three build subflows then the boot
subflow. ``resolve`` maps the reuse picks to a concrete boot manifest
(the kernel image, modules, emulator binaries) and raises loudly on an
unresolvable pick, so a stale dropdown choice fails before anything
builds or boots. Each build subflow runs only when its component's mode
is ``build``; the remaining groups (QEMU machine, Kernel boot,
Networking, File sharing, NVMe, Orchestration) are :doc:`boot`'s form,
embedded unchanged and documented there.

The flow definition is generated. ``scripts/gen-bringup.py`` composes
it from the subflows it embeds plus the bringup-level transforms in the
script itself, and the flake's ``generated`` check fails when the
committed flow drifts from the generator output. To change the flow,
edit the source subflow schema or the generator and regenerate; never
hand-edit ``f/qsu/bringup.flow/flow.yaml``.

Watching a run
==============

The job log is the primary view (:doc:`guests`). Each enabled build
subflow streams its own steps exactly as the standalone flow does (the
kernel build's compile log, the closure build, the QEMU build), and the
boot tail ends with the guest's access manifest as the flow result.
After the run the guest is host-native systemd state; :doc:`guests`
covers reaching, querying, and stopping it, and the per-suite flow
pages cover running tests against it.
