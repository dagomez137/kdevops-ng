.. SPDX-License-Identifier: copyleft-next-0.3.1

=================
Inspecting guests
=================

This page covers what is common to every :doc:`flow <../concepts/flows>` that
drives a guest, independent of the test suite or benchmark it runs.

Windmill is the primary view
============================

A flow that runs work on a guest polls it from a ``wait`` (or equivalent) step,
and that step streams the guest's combined unit and kernel journal into the
`Windmill`_ job log as the run proceeds. So the normal way to watch progress,
:cmd:`dmesg`, and the final verdict is the Windmill UI: the job log already
carries it, live.

The command-line recipes on the per-flow pages are the out-of-band view. Reach
for them to inspect or intervene on the guest directly, independently of the
job: when a run looks stuck, when you want to drive systemd on the guest by
hand, or when you are working on the guest without a job in flight at all.

Reaching a guest from the command line
======================================

Flows reach a guest over vsock-SSH, using the kdevops-managed SSH config that
:src:`f/workbench/init` generates: a ``Host <vm>`` block per booted VM, dialing
its ``vsock/<cid>`` through :cmd:`systemd-ssh-proxy`. Add the one-line
``Include`` of that config to your :cmd:`~/.ssh/config` (see
:doc:`../deployment/nix`) and the VM name becomes an ordinary :cmd:`ssh` alias,
so ``ssh <vm> …`` works.

With that alias in place, systemd's own remote flag reaches the guest's systemd
over the same ``ssh`` transport. Most systemd client tools (:cmd:`systemctl`,
:cmd:`timedatectl`, :cmd:`loginctl`, :cmd:`systemd-analyze`) accept
``-H``/``--host <vm>`` and work this way, talking to the guest's system bus:

.. code-block:: console

   $ systemctl --host <vm> status <unit>      # units, services, scopes
   $ timedatectl --host <vm>                  # clock and timezone
   $ loginctl --host <vm> list-sessions       # sessions and seats
   $ systemd-analyze --host <vm> blame        # boot timing

This is the form to prefer for anything bus-based: it is the same mechanism the
step code uses, and it keeps you in systemd's own vocabulary.

:cmd:`journalctl` is the exception. Its ``--host`` transport speaks to
:cmd:`systemd-journal-gatewayd` over HTTP, which the guests do not run, so
reading the journal goes through plain ``ssh``, running ``journalctl`` as a
remote command on the guest:

.. code-block:: console

   $ ssh <vm> journalctl --unit=<unit> --follow

Running a tool as a remote ``ssh`` command is the universal fallback in general:
use it whenever ``-H`` cannot reach a target, or for a guest-side tool that has
no ``-H`` flag at all.

Machine and VM status
=====================

Distinct from reaching *into* a guest, these commands act on the VM as it looks
from the host: a systemd user service, ``qemu-system@<vm>.service``, a per-VM
instance of the ``qemu-system@.service`` template unit. The `qemu-system-units`_
project that supplies the template documents the full command set, summarised
here.

The VM registers itself with :cmd:`systemd-machined`, so it also shows up as a
machine. That registry is per-user, so :cmd:`machinectl` needs ``--user`` to
reach it (systemd v259 or newer):

.. code-block:: console

   $ machinectl --user list             # registered machines (VMs, containers)
   $ machinectl --user status <vm>      # one VM's PID, cgroup, service
   $ machinectl --user terminate <vm>   # emergency kill, not graceful

For a dependable list of the VMs running on the host, read the user manager's
units directly; the unit list is authoritative, independent of the machined
registry:

.. code-block:: console

   $ systemctl --user list-units 'qemu-system@*'   # VMs on this host
   $ systemctl --user status qemu-system@<vm>      # one VM's service state

Read a VM's host-side log (boot console on ``ttyS0``, QEMU and virtiofsd
messages) through the user journal:

.. code-block:: console

   $ journalctl --user-unit=qemu-system@<vm>.service --follow   # one VM, live
   $ journalctl --user-unit='qemu-system@*.service' --no-hostname --follow

A persistent VM also exposes a virtio console socket for interactive access;
attach with :cmd:`socat` (disconnect with ``Ctrl-]``, the VM keeps running):

.. code-block:: console

   $ socat -,raw,echo=0,escape=0x1d \
       UNIX-CONNECT:$XDG_RUNTIME_DIR/qemu-system/<vm>/console.sock

Stopping is graceful by default: a clean guest powerdown, with systemd
escalating to ``SIGKILL`` if it overruns the stop timeout; ``restart`` does that
stop, then boots a fresh guest (it is how a VM is re-launched):

.. code-block:: console

   $ systemctl --user restart qemu-system@<vm>               # fresh reboot
   $ systemctl --user stop qemu-system@<vm>                  # graceful stop
   $ systemctl --user kill qemu-system@<vm> --signal=SIGKILL # force kill
   $ systemctl --user stop machines.target                   # stop every VM
   $ systemctl --user reset-failed qemu-system@<vm>          # clear failed

To check a guest's own health from the host instead, go through ``--host`` into
its system bus:

.. code-block:: console

   $ systemctl --host <vm> is-system-running   # guest systemd state
   $ hostnamectl --host <vm>                   # guest identity

.. _Windmill: https://www.windmill.dev/
.. _qemu-system-units: https://github.com/linux-kdevops/qemu-system-units
