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
once with sudo; afterwards the passthrough runs in user mode. Load the driver,
install the udev rule that lets the ``kvm`` group open the VFIO nodes, and
reload udev:

.. code-block:: console

   $ sudo cp vendor/qemu-system-units/files/vfio-pci.conf \
       /etc/modules-load.d/vfio-pci.conf
   $ sudo modprobe vfio-pci
   $ minijinja-cli --trim-blocks \
       vendor/qemu-system-units/templates/vfio-udev.rules.j2 <vm>.yaml \
       | sudo tee /etc/udev/rules.d/10-vfio-kvm.rules
   $ sudo udevadm control --reload-rules
   $ sudo udevadm trigger --subsystem-match=pci

The rule sets ``SUBSYSTEM=="vfio", GROUP="kvm", MODE="0660"`` so the ``kvm``
group can open ``/dev/vfio``, with a per-device block for each address in the
VM's ``pci_passthrough``. Skip this section unless a flow uses passthrough.
