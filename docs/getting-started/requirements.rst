.. SPDX-License-Identifier: copyleft-next-0.3.1

============
Requirements
============

kdevops-ng targets a Linux distribution with two things installed: `systemd`_
and `Nix`_ (the package manager). The host's systemd runs the `Windmill`_ stack
and the guest VMs as ``systemd --user`` services, and Nix builds and runs
everything else: the Windmill server, the kernels and QEMU under test, and the
developer tooling. So the host stays minimal: it needs no distro QEMU or build
packages, only Nix and a little kernel-level access for the guests.

kdevops-ng needs systemd as the init system. ``systemctl is-system-running``
reports the manager state (``running``, or ``degraded`` if a unit has failed),
and is absent or errors where systemd is not PID 1. The underlying check, the
one ``sd_booted`` performs, is whether ``/run/systemd/system`` exists. Modern
distributions satisfy this by default.

Nix
===

Install Nix with the recommended multi-user (daemon) installation:

.. code-block:: console
   :caption: host
   :class: cmd-host

   $ curl --proto '=https' --tlsv1.2 -L https://nixos.org/nix/install \
       | sh -s -- --daemon

See the `Nix install guide`_ for other installers and platform notes.

.. _Nix install guide: https://nixos.org/download/#nix-install-linux

The flake uses the unified ``nix`` CLI, which needs the experimental features
enabled once:

.. code-block:: console
   :class: cmd-host

   $ mkdir --parents ~/.config/nix
   $ echo 'experimental-features = nix-command flakes' \
       | tee --append ~/.config/nix/nix.conf

Host access for guests
======================

Running the guest VMs (the :src:`f/qsu` flows) needs kernel-level access granted
once. Add yourself to the ``kvm`` group for ``/dev/kvm`` (QEMU's ``-accel kvm``)
and ``systemd-journal`` to read service logs without sudo:

.. code-block:: console
   :class: cmd-host

   $ sudo usermod --append --groups kvm,systemd-journal "$(whoami)"

Log out and back in for the group change to take effect.

Load the ``vhost_vsock`` module, which the guest transport (SSH over vsock, see
:doc:`/flows/guests`) and the per-VM ``qemu-system@`` units need, and have it
loaded at boot. The ``kvm`` group already owns ``/dev/vhost-vsock`` through
systemd's stock udev rules:

.. code-block:: console
   :class: cmd-host

   $ echo vhost_vsock | sudo tee /etc/modules-load.d/vhost_vsock.conf
   $ sudo modprobe vhost_vsock

PCI passthrough (VFIO)
======================

Letting a guest take a host PCI device (a GPU, an NVMe drive) needs VFIO. This
is a one-time root setup, done by hand, that hands the devices you choose to the
``vfio-pci`` driver, lets the ``kvm`` group open them, and lets your user
manager lock a guest's RAM; after it, a flow passes a device to a guest in user
mode, with no sudo at VM time. kdevops-ng never runs these commands for you. Do
it only for devices you intend to make available; skip the section otherwise.

VFIO needs the IOMMU. Enable it in the firmware (VT-d or AMD-Vi); an Intel host
whose kernel does not turn it on by default (``CONFIG_INTEL_IOMMU_DEFAULT_ON``)
also needs ``intel_iommu=on`` on its command line. Once it is on,
``/sys/kernel/iommu_groups`` holds one directory per group.

First list the host PCI devices with :cmd:`lspci` and note the address of each
candidate. The first column is the address, with the ``0000:`` domain shown by
``-D``; every endpoint that shares a device's IOMMU group goes with it (a GPU's
audio function usually does):

.. code-block:: console
   :class: cmd-host

   $ lspci -nn -D
   0000:81:00.0 3D controller [0302]: NVIDIA Corporation ... [10de:2330]
   0000:81:00.1 Audio device [0403]: NVIDIA Corporation ... [10de:22a3]

Record the addresses in a small YAML file, say ``passthrough.yaml``:

.. code-block:: yaml

   pci_passthrough:
     - addr: "0000:81:00.0"
     - addr: "0000:81:00.1"

Load the ``vfio-pci`` driver with :cmd:`modprobe`, now and at every boot:

.. code-block:: console
   :class: cmd-host

   $ sudo cp vendor/qemu-system-units/files/vfio-pci.conf \
       /etc/modules-load.d/vfio-pci.conf
   $ sudo modprobe vfio-pci

Render the udev rule from your file and install it (:cmd:`minijinja-cli` comes
from the development shell). It sets ``SUBSYSTEM=="vfio", GROUP="kvm",
MODE="0660"`` so the ``kvm`` group can open ``/dev/vfio``, and for each address
sets the device's ``driver_override`` to ``vfio-pci`` when it appears, so the
kernel binds it to ``vfio-pci`` instead of its host driver:

.. code-block:: console
   :class: cmd-host

   $ nix develop --command minijinja-cli --trim-blocks \
       vendor/qemu-system-units/templates/vfio-udev.rules.j2 passthrough.yaml \
       | sudo tee /etc/udev/rules.d/10-vfio-kvm.rules

Reload udev. The rule binds each device at the next boot; to move a device a
host driver holds now, replay its add event with :cmd:`udevadm`, which sets the
override, then unbind it so the kernel probes it again. Do it for every address
in the file:

.. code-block:: console
   :class: cmd-host

   $ sudo udevadm control --reload-rules
   $ sudo udevadm trigger --action=add /sys/bus/pci/devices/0000:81:00.0
   $ echo 0000:81:00.0 \
       | sudo tee /sys/bus/pci/devices/0000:81:00.0/driver/unbind
   $ echo 0000:81:00.0 | sudo tee /sys/bus/pci/drivers_probe
   $ readlink /sys/bus/pci/devices/0000:81:00.0/driver   # ends in vfio-pci

A passthrough guest locks its RAM for DMA, so its ``qemu-system@`` unit asks
for ``LimitMEMLOCK=<ram+256>M``. A user unit cannot exceed the hard limit of
your user's service manager, which inherits the system default of 8 MiB, so
raise it once for :cmd:`user@.service` with a drop-in (``infinity``, or a size
that covers your largest passthrough guest plus 256M):

.. code-block:: console
   :class: cmd-host

   $ sudo mkdir --parents /etc/systemd/system/user@.service.d
   $ printf '[Service]\nLimitMEMLOCK=infinity\n' \
       | sudo tee /etc/systemd/system/user@.service.d/memlock.conf
   $ sudo systemctl daemon-reload

The limit reaches your service manager when it next starts, which stops every
unit it runs, the Windmill stack and the guests included: restart it with
``sudo systemctl restart "user@$(id --user).service"`` from a session you can
afford to lose, or reboot. ``systemctl show --property=LimitMEMLOCK
"user@$(id --user).service"`` shows what it got.

Pick the same devices in the Passthrough group of the boot or bringup form; the
picker lists each host device with its IOMMU group and driver, GPUs first.
Before the VM starts, the render refuses a partial group, a device the IOMMU
left ungrouped, a host without the ``vfio-pci`` driver loaded, a device the udev
rule has not bound to ``vfio-pci``, and a user manager whose locked-memory limit
is below the guest's RAM plus 256M, each pointing back here. ``vfio-pci
Options`` takes extra ``-device vfio-pci`` properties (``rombar=0`` drops the
option-ROM BAR); list them with :cmd:`qemu-system-x86_64` ``-device
vfio-pci,help``, and see the QEMU `device emulation`_ guide and the kernel
`VFIO`_ docs.

.. _device emulation: https://www.qemu.org/docs/master/system/device-emulation.html
.. _VFIO: https://docs.kernel.org/driver-api/vfio.html

.. _user-configuration:

Your own configuration
======================

kdevops-ng never edits a configuration file of yours. Where a host tool needs
to know about something kdevops-ng manages, you add one line yourself, once,
like the root steps above.

SSH. The System workbench keeps the guests' key and a ``Host <vm>`` block per
booted guest in ``SYSTEM_DIR/ssh/config``. Add an ``Include`` of it at the top
of ``~/.ssh/config`` so ``ssh <vm>`` reaches a guest over vsock with the managed
key. Use the absolute path, since ssh resolves a relative ``Include`` against
``~/.ssh``; with the default Workbench it is:

.. code-block:: text

   Include ~/.local/state/windmill/workbench/system/ssh/config

The workbench init flow prints the exact line for your ``SYSTEM_DIR``.

Git. kdevops-ng needs no setting in ``~/.gitconfig`` and reads none: the
workers run as you and own every tree they touch, the committer identity
``git am`` needs is passed on its own command line, and the worker unit points
git at an empty global config. Earlier versions added
``safe.directory = *``, which switches git's ownership check off for all your
repositories; if ``git config --global --get-all safe.directory`` lists
``*``, remove it:

.. code-block:: console
   :class: cmd-host

   $ git config --global --unset-all safe.directory '^\*$'

.. _systemd: https://systemd.io
.. _Nix: https://nixos.org
.. _Windmill: https://windmill.dev
