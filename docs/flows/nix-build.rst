.. SPDX-License-Identifier: copyleft-next-0.3.1

:orphan:

=======================
Build the NixOS closure
=======================

The `f/nix/build`_ flow builds a NixOS system with Nix, the way
`f/kernel/build`_ builds a kernel and `f/qemu/build`_ builds an emulator. Today
it builds the *imageless* product: a ``toplevel`` system whose closure (every
store path it references) a VM boots over virtiofs, with a tmpfs root and
``/nix/store`` and ``/lib/modules`` shared from the host. Other products, such
as a disk-image ``libvirt`` system, could be added under the same flow later.

The flow is the Windmill reimplementation of kdevops's ``nixosfi`` role. It
covers only the build half: render, lock, then build the closure. Booting the
closure is the QEMU/systemd half, a separate concern (host-systemd VM
lifecycle) that is parked and described elsewhere; it is mentioned here only
where the two meet.

The flow is thin and runs all three steps on one worker slot, pinned with
``same_worker: true`` so each step sees the per-VM config directory the previous
step wrote under ``$WORKERS_DIR/$WORKER_INDEX/nix/<vm_name>/``:

1. ``render_config``: write ``flake.nix`` (from the vendored imageless template)
   plus ``default.nix`` (composed from the typed inputs: profiles, test suites,
   shares, SSH keys, source overrides).
2. ``lock_config``: ``nix flake lock`` the per-VM config for reproducibility.
3. ``build_closure``: ``nix build .#toplevel`` and read the bootspec
   (``init`` and ``initrd``) from ``boot.json``.

The flow returns ``{toplevel, init, initrd, config_dir}``. It is self-contained:
pairing the closure with a kernel is a QEMU/systemd concern, not an input here.

Featured by default
===================

With no ``profiles`` or ``test_suites`` passed, the system is fully featured:
every guest profile (``devel``, ``build-tools``, ``monitoring``) and all eight
test suites. The ``devel`` and ``build-tools`` profiles are active on import;
``monitoring`` is gated, so render emits ``nixos-flake.monitoring.enable =
true`` whenever it (or any gated profile) is selected. ``controller`` is a host
role: it pulls libvirtd into the guest, and upstream only composes it on the
libvirt backend, so it is an available option but off by default. Pare the
``profiles`` and ``test_suites`` lists back per run for a lighter, faster build.

The per-VM config contract
==========================

The unit of customization is a per-VM configuration directory with its own
``flake.nix``, ``default.nix`` and ``flake.lock``, not one global flake rebuilt
in place. This is the idiomatic "Multiple configurations" shape from the
vendored ``nixos-flake`` library.

The directory wires itself to the vendored library and a single nixpkgs:

- ``inputs.nixos-flake.url`` points at ``path:<abs>/vendor/nixos-flake``, the
  copy this repository already vendors.
- ``inputs.nixpkgs.follows = "nixos-flake/nixpkgs"`` avoids a second nixpkgs.

The ``flake.nix`` is essentially static. The library and its inputs are passed
through ``specialArgs``, so all per-VM composition (imports, overlays, hostname,
SSH keys) lives in ``default.nix``. The only thing that varies the ``flake.nix``
is an optional per-package source override input.

The imageless kernel is external (``boot.kernel.enable = false``), so the kernel
image and ``/lib/modules`` come from `f/kernel/build`_, not from the closure.
That kernel must have ``CONFIG_VIRTIO_FS=y``, ``CONFIG_VIRTIO_PCI=y`` and
``CONFIG_TMPFS=y`` built in, which is exactly what the ``imageless_defconfig``
preset guarantees. The clean closure path is therefore: preset config, then
kernel, then imageless boot.

Footguns to honor
-----------------

A few properties of the ``path:`` scheme shape how the flow generates and builds
the config:

- The flow builds with ``nix build path:<dir>#toplevel``, as kdevops does. The
  ``path:`` fetcher copies the whole config directory into the store, so the
  generated files do not need to be git-tracked. The "flakes only see
  git-tracked files" rule applies only to bare and ``git+file`` flakerefs, so
  the flow runs no ``git init``.
- The ``path:`` scheme does not expand ``~``. The flow uses absolute
  ``WORKERS_DIR`` paths.
- ``flake.lock`` is kept per config for reproducibility (the ``lock_config``
  step). Re-pinning the vendored library is ``nix flake update --flake
  path:<dir> nixos-flake``, exposed through the ``update_lock`` input.
- Because ``boot.kernel.enable = false``, the closure has no ``$out/kernel`` or
  ``$out/initrd`` symlinks. The ``init`` and ``initrd`` come from the standard
  NixOS bootspec (RFC-0125) at ``<toplevel>/boot.json``, under the
  ``org.nixos.bootspec.v1`` key.

Why a rendered config, not a generated flake
============================================

The vendored library is consumed in two unrelated ways, and the right answer to
"should we generate our own flake?" is opposite for each.

A kernel build is deliberately not a Nix derivation. There, Nix's role is to
provide a toolchain (GCC, make, bison and friends) and the pipeline decides
which compiler, flags and targets to use inside the resulting shell:
``f/kernel/build`` runs ``nix develop .#build --command make ...``. Generating a
flake to compile a kernel would be a category error, so ``f/kernel`` stays on
``#build`` and produces no per-build flake.

A NixOS closure is the opposite: the closure is the derivation, so building it
does require a per-VM config flake, invoked as ``nix build
path:<dir>#toplevel``. That is the flake this flow renders.

Even on the closure side the ``flake.nix`` itself is not freely generated: it is
rendered near-verbatim from the vendored ``templates/imageless/flake.nix``, with
only ``nixos-flake.url`` set to the vendored absolute path (and one source
override input per override). Upstream's canonical flow says ``nix flake init
--template``; kdevops instead mirrors the templates and renders them, and this
flow makes the same choice and renders from the same vendored
``templates/imageless/`` source kdevops mirrors. The closure built here is
therefore byte-for-byte what kdevops builds; only the driver differs.

Relationship to the kdevops nixosfi role
========================================

The Nix-facing contract is identical to kdevops's ``nixosfi`` role; only the
orchestration is reimplemented. Both consume the same vendored library, produce
the same per-VM artifact shape (``flake.nix`` plus ``default.nix`` plus
``flake.lock``), import the same module sets, build with ``nix build
path:<dir>#toplevel``, and read the boot artifact from the closure's bootspec.

What differs is the machinery that decides which modules to compose and drives
the phases. The ``nixosfi`` role takes Kconfig output to Ansible variables to
Jinja2 ``{% if %}`` blocks, templated with Jinja2 and orchestrated as an Ansible
role with tag-gated phases. This flow takes Windmill flow inputs (a JSON schema)
to step code, templated by a plain Python string builder and orchestrated as a
Windmill flow of step modules. The result is equivalent, reimplemented.

Wrapping the kdevops playbook directly (``ansible-playbook nixosfi.yml``) would
be literally identical but would drag in the whole kdevops tree, Ansible,
Kconfig and inventory, against the granular native-steps direction. Inventing a
different closure shape would lose the "same closure as kdevops" guarantee for
no benefit. So the flow reimplements the orchestration and keeps the contract.

Jinja2 to Python
----------------

The one engine swap worth calling out is templating. The ``nixosfi`` role
renders ``default.nix`` with Jinja2; this flow generates it with a plain Python
string builder from the typed step inputs, so the steps carry no Jinja2
dependency. The mapping is direct: each selected profile and test suite becomes
an entry in the ``imports`` list, ``shares`` adds the mounts module, and the
hostname, user name, SSH keys and source overrides become the corresponding
attributes. The ``render_config`` step prints both rendered files before
returning, the same debuggability discipline the runners apply to commands.

Why f/nix
=========

Nix is the umbrella and NixOS is one thing built with it; every operation in
this flow goes through the ``nix`` CLI. A single ``f/nix`` bucket cleanly holds
both "run a package" (the existing ``f/nix/hello`` is ``nix run
nixpkgs#hello``) and "build a NixOS system", because both are Nix operations.
Booting the closure is not a Nix operation (it is host-systemd VM lifecycle), so
it stays in a separate QEMU/systemd bucket under ``f/qsu/``. This mirrors
kdevops's own split, where the ``nixosfi`` role builds the closure and a
separate role boots it.

Composition with the kernel build
=================================

The closure build and the kernel build are independent. The flow takes no kernel
input: the closure sets ``boot.kernel.enable = false`` and its initrd loads no
modules, so it builds entirely on its own. ``f/kernel/build`` separately
produces the ``bzImage`` and the ``/lib/modules`` tree.

The two products meet only at boot, where the QEMU/systemd half pairs the
``toplevel`` closure with the external kernel and modules and starts the
machine. Because the build half needs no host systemd, the entire render, lock
and build sequence is runnable and provable on its own, before the boot half
exists.

.. _f/nix/build:
   https://github.com/dagomez137/kdevops-ng/tree/main/f/nix/build.flow
.. _f/kernel/build:
   https://github.com/dagomez137/kdevops-ng/tree/main/f/kernel/build.flow
.. _f/qemu/build:
   https://github.com/dagomez137/kdevops-ng/tree/main/f/qemu/build.flow
