.. SPDX-License-Identifier: copyleft-next-0.3.1

========
Overview
========

kdevops-ng is a Linux kernel development and test-automation framework. It
self-hosts a Windmill workflow engine and uses Nix for reproducible build and
guest environments. The flows, steps and apps that drive the work are kept as
code in git, which is the source of truth; ``wmill`` moves them between the
repository and the running instance. The instance binds ``127.0.0.1:8000``;
open it in a browser on the host, or forward the port over SSH if the host is
remote.

.. _project-status:

Project status
==============

kdevops-ng is a proof of concept, not a separate product; "ng" is the working
name for this proposal, not a second project. The intent is to merge it
upstream into `kdevops`_ once the community has discussed the direction. The
proposal and early maintainer feedback live in the `mailing-list thread`_; read
it before assuming anything here is settled.

.. _kdevops: https://github.com/linux-kdevops/kdevops
.. _mailing-list thread: https://lore.kernel.org/all/9f64bee9-ecc3-4587-9645-2190223cbc4e@kernel.org/

Merging upstream is deliberately staged: additive first, convergence later.

**Additive first.** The new path uses Nix for the reproducible environment,
Windmill for workflow orchestration, and systemd for execution; it arrives
alongside the existing Kconfig + Make + Ansible path and never threatens it.
Nix supplies that environment identically across the controller and every
target, VM guest or baremetal. Within that path, QEMU guests already run as
systemd user services (QSU, in kdevops today), and ng extends this to the test
runs, so guests and suites alike are started, stopped, monitored and logged
through the process manager. Nothing a current user relies on changes, so
adoption is opt-in and requires no migration.

The new path is fully independent of the old one. That independence is the
point: it is what lets the two coexist without entanglement now, and what would
make a later deprecation a clean removal rather than surgery.

**Convergence later.** I believe the Nix + Windmill + systemd path is where
kdevops should ultimately land, and this proposal does not pretend otherwise.
But any deprecation of the old path is a later step the community signs off on,
gated on a working migration already being in place. Trying the new path never
requires accepting that endpoint.

Repository layout
=================

::

   kdevops-ng/
   ├── f/          Windmill workspace content (flows, steps, apps)
   ├── deploy/     brings the Windmill instance up (podman today)
   ├── vendor/     pinned upstream projects (nixos-flake, ...)
   ├── docs/       this documentation site and the ADRs
   ├── scripts/    check scripts and generators
   ├── wmill.yaml  workspace-as-code configuration
   └── flake.nix   nix tooling: checks, apps, devShells, formatter

The big pieces:

- ``f/`` holds the workspace content, grouped into subsystems and named by a
  small convention; see :doc:`/concepts/flows` and :doc:`/reference/wmill-yaml`.
- ``deploy/`` deploys the Windmill instance; the ``nix`` deployment is the
  current default, with ``podman`` retired and ``distro`` planned.
- ``vendor/`` carries pinned upstream projects the flows build against.
- ``docs/`` is this site, together with the architecture decision records
  under ``docs/adr/``.

The on-disk area where kernels and QEMU are actually built is the *workbench*,
which is runtime and not tracked in git; see :doc:`/concepts/terms`.
