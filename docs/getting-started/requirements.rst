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

Passing a host PCI device (an NVMe drive, say) into a guest needs VFIO, set up
once with sudo; afterwards the passthrough runs in user mode.

First find the address of each device to pass through. ``lspci`` lists them; the
first column is the address, with the ``0000:`` domain shown by ``-D``:

.. code-block:: console

   $ lspci -nn -D
   0000:2d:00.0 Non-Volatile memory controller [0108]: Samsung ... [144d:a80a]

List the addresses in a small YAML file, say ``passthrough.yaml``; ``opts`` is
an optional per-device QEMU device suffix:

.. code-block:: yaml

   pci_passthrough:
     - addr: "0000:2d:00.0"
     - addr: "0000:03:00.0"
       opts: "rombar=0"

Then load the driver, render the udev rule from that file, install it, and
reload udev (``minijinja-cli`` comes from the dev shell):

.. code-block:: console

   $ sudo cp vendor/qemu-system-units/files/vfio-pci.conf \
       /etc/modules-load.d/vfio-pci.conf
   $ sudo modprobe vfio-pci
   $ nix develop --command minijinja-cli --trim-blocks \
       vendor/qemu-system-units/templates/vfio-udev.rules.j2 passthrough.yaml \
       | sudo tee /etc/udev/rules.d/10-vfio-kvm.rules
   $ sudo udevadm control --reload-rules
   $ sudo udevadm trigger --subsystem-match=pci

The rule sets ``SUBSYSTEM=="vfio", GROUP="kvm", MODE="0660"`` so the ``kvm``
group can open ``/dev/vfio``, with a per-device block for each address. Skip
this section unless a flow uses passthrough.
