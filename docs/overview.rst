.. SPDX-License-Identifier: copyleft-next-0.3.1

========
Overview
========

kdevops-ng is a Linux kernel development and test-automation framework. It
self-hosts a Windmill workflow engine and uses Nix for reproducible build and
guest environments. The flows, steps and apps that drive the work are kept as
code in git, which is the source of truth; ``wmill`` moves them between the
repository and the running instance. The instance runs locally and is reached
on ``127.0.0.1:8000`` (SSH-forward to use the UI).

Repository layout
=================

::

   kdevops-ng/
   ├── f/          Windmill workspace content (flows, steps, apps)
   ├── deploy/     brings the Windmill instance up (podman today)
   ├── vendor/     pinned upstream projects (nixos-flake, ...)
   ├── docs/       this documentation site and the ADRs
   ├── scripts/    make-style checks and generators
   ├── wmill.yaml  workspace-as-code configuration
   └── Makefile    style, generated, docs and serve targets

The big pieces:

- ``f/`` holds the workspace content, grouped into subsystems and named by a
  small convention; see :doc:`flows` and :doc:`wmill-yaml`.
- ``deploy/`` brings up the Windmill instance; the ``podman`` backend works
  today, with ``distro`` and ``nix`` backends planned.
- ``vendor/`` carries pinned upstream projects the flows build against.
- ``docs/`` is this site, together with the architecture decision records
  under ``docs/adr/``.

The on-disk area where kernels and QEMU are actually built is the *workbench*,
which is runtime and not tracked in git; see :doc:`terms`.
