.. SPDX-License-Identifier: copyleft-next-0.3.1

==========
Principles
==========

Three rules hold everywhere in kdevops-ng: in the flows and steps, the
deployment, the scripts, and any change proposed to them. Each one is a
reason something is built the way it is, and a change that breaks one is
reworked rather than merged.

Nix first
=========

Every tool, library, service and guest component comes from `Nix`_, pinned.
The host needs only Linux, `systemd`_ and Nix (see
:doc:`/getting-started/requirements`); everything else is built from the
pinned inputs, so two hosts given the same inputs build the same bytes. That
is what lets a host fetch a kernel another host built instead of rebuilding
it (:doc:`build-store`), and what makes a bug found on one machine
reproducible on another.

In practice:

- The `Windmill`_ server, its workers and its database are built from Nix and
  pinned to one revision (:doc:`/deployment/nix`).
- Steps run every external command inside a pinned Nix development shell,
  never a binary that happens to be on the host's ``PATH``.
- Guests boot a `NixOS`_ closure built from the vendored ``nixos-flake``
  (:doc:`/flows/nix-build`).
- A tool `nixpkgs`_ lacks is packaged in the vendored flake and stays
  overridable, rather than fetched while a job runs.

On the host there is no distro-package install, no container runtime and no
``pip`` or ``npm`` install outside a Nix derivation.

systemd first
=============

Anything that runs for a while, needs supervising, or must be stopped
cleanly is a systemd unit, and the project leans on what systemd already
does instead of writing its own. Starting, stopping, restarting, timeouts,
dependencies, logging, scheduling and resource limits all come from the
service manager; there are no custom daemons, wrapper scripts, PID files,
polling loops or cron jobs.

In practice:

- The Windmill stack runs as ``systemd --user`` services, and the mirrors
  refresh from ``git-mirror@`` timers.
- Each guest is a ``qemu-system@`` service with its :cmd:`virtiofsd` sockets
  and its registration with :cmd:`systemd-machined`, rendered from the
  vendored `qemu-system-units`_ templates.
- Inside a guest, every test suite runs as a unit (``xfstests@``,
  ``blktests@``, ``kunit@``, ``kselftest@``, ``usertests@``, the stock
  ``modprobe@``), and a single test that must be killable runs in a
  transient scope, started with :cmd:`systemd-run` or, where the runner
  enrols itself, over the bus with ``StartTransientUnit``
  (:doc:`/contributing/test-suites`).
- The journal is the log and the results channel, and a flow reaches a
  guest's systemd over SSH, running :cmd:`systemctl` there as you would
  with ``systemctl --host`` (:doc:`/flows/guests`).
- A unit gets its settings from systemd's own mechanisms, environment
  files, specifiers and drop-ins, and an upstream template is preferred to
  writing a new one.

User mode only
==============

Everything kdevops-ng runs, it runs as you, an unprivileged user, and it
drives your user's service manager (``systemctl --user``), never the system
one. Nothing in the project runs ``sudo`` on the host.

A few things genuinely need root: the ``kvm`` and ``systemd-journal`` groups,
the ``vhost_vsock`` module the guest transport needs, the ``vfio-pci`` module
and its udev rules for PCI passthrough, and the locked-memory limit a
passthrough guest needs. Each is a one-time step you run
yourself, documented with the exact commands in
:doc:`/getting-started/requirements`. No flow or script runs, wraps or
prints those commands for you. When one is missing, the step that needs it
says so and points you back to that page.

Keeping root out of the steady state means a flow can never change the host
behind your back, and what a run did is what your user could do.

.. _Nix: https://nixos.org
.. _systemd: https://systemd.io
.. _Windmill: https://www.windmill.dev/
.. _NixOS: https://nixos.org/
.. _nixpkgs: https://github.com/NixOS/nixpkgs
.. _qemu-system-units: https://github.com/linux-kdevops/qemu-system-units
