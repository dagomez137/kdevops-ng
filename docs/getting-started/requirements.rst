.. SPDX-License-Identifier: copyleft-next-0.3.1

============
Requirements
============

kdevops-ng targets a Linux distribution with two things installed: systemd and
Nix. The host's systemd runs the Windmill stack and the guest VMs as
``systemd --user`` services, and Nix builds and runs everything else: the
Windmill server, the kernels and QEMU under test, and the developer tooling. So
the host stays minimal: it needs no distro QEMU or build packages, only Nix and
a little kernel-level access for the guests.

kdevops-ng needs systemd as the init system. ``systemctl is-system-running``
reports the manager state (``running``, or ``degraded`` if a unit has failed),
and is absent or errors where systemd is not PID 1. The underlying check, the
one ``sd_booted`` performs, is whether ``/run/systemd/system`` exists. Modern
distributions satisfy this by default.

Nix
===

Install Nix with the recommended multi-user (daemon) installation:

.. code-block:: console

   $ curl --proto '=https' --tlsv1.2 -L https://nixos.org/nix/install \
       | sh -s -- --daemon

See the `Nix install guide`_ for other installers and platform notes.

.. _Nix install guide: https://nixos.org/download/#nix-install-linux

The flake uses the unified ``nix`` CLI, which needs the experimental features
enabled once:

.. code-block:: console

   $ mkdir --parents ~/.config/nix
   $ echo 'experimental-features = nix-command flakes' \
       | tee --append ~/.config/nix/nix.conf

Host access for guests
======================

Running the guest VMs (the ``f/qsu`` flows) needs kernel-level access granted
once. Add yourself to the ``kvm`` group for ``/dev/kvm`` (QEMU's ``-accel kvm``)
and ``systemd-journal`` to read service logs without sudo:

.. code-block:: console

   $ sudo usermod --append --groups kvm,systemd-journal "$(whoami)"

Log out and back in for the group change to take effect.

PCI passthrough (VFIO)
======================

Letting a guest take a host PCI device (an NVMe drive, say) needs VFIO. This is
a one-time sudo setup that grants the ``kvm`` group access to the devices you
may want to hand to guests; after it, a flow binds and passes a device in user
mode, with no sudo at VM time. Do it only for devices you intend to make
available; skip the section otherwise.

First list the host PCI devices and note the address of each candidate. The
first column is the address, with the ``0000:`` domain shown by ``-D``:

.. code-block:: console

   $ lspci -nn -D
   0000:2d:00.0 Non-Volatile memory controller [0108]: Samsung ... [144d:a80a]

Record the addresses in a small YAML file, say ``passthrough.yaml``:

.. code-block:: yaml

   pci_passthrough:
     - addr: "0000:2d:00.0"
     - addr: "0000:03:00.0"
       opts: "rombar=0"

``opts`` is an optional string of extra QEMU ``-device vfio-pci`` properties
(``rombar=0`` drops the option-ROM BAR). List the available ones with
``qemu-system-x86_64 -device vfio-pci,help``; see the QEMU `device emulation`_
guide for the syntax and the kernel `VFIO`_ docs for the framework.

.. _device emulation: https://www.qemu.org/docs/master/system/device-emulation.html
.. _VFIO: https://docs.kernel.org/driver-api/vfio.html

Load the ``vfio-pci`` driver:

.. code-block:: console

   $ sudo cp vendor/qemu-system-units/files/vfio-pci.conf \
       /etc/modules-load.d/vfio-pci.conf
   $ sudo modprobe vfio-pci

Render the udev rule from your file and install it (``minijinja-cli`` comes from
the development shell). It sets ``SUBSYSTEM=="vfio", GROUP="kvm", MODE="0660"``
so the
``kvm`` group can open ``/dev/vfio``, with a per-device block for each address:

.. code-block:: console

   $ nix develop --command minijinja-cli --trim-blocks \
       vendor/qemu-system-units/templates/vfio-udev.rules.j2 passthrough.yaml \
       | sudo tee /etc/udev/rules.d/10-vfio-kvm.rules

Reload udev so the rule takes effect:

.. code-block:: console

   $ sudo udevadm control --reload-rules
   $ sudo udevadm trigger --subsystem-match=pci
