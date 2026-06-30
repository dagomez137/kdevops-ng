.. SPDX-License-Identifier: copyleft-next-0.3.1

:orphan:

===================================
Dumping a wedged guest with SysRq
===================================

:doc:`Inspecting guests <guests>` assumes the guest is far enough along to
answer over the SSH transport. A guest that wedges before that, in early boot
or during activation, has no shell to reach: ``ssh <vm>`` hangs, and the job
log stops advancing with no verdict. This page covers the out-of-band way to
make such a guest tell you *where* it is stuck, by injecting a `SysRq`_ command
into its emulated keyboard and reading the dumped task stacks back from the
host journal.

What SysRq is
=============

`SysRq`_ is the kernel's "magic system request" facility: a set of low-level
debug commands the kernel honours even when userspace is unresponsive, gated by
``CONFIG_MAGIC_SYSRQ=y`` (set in ``imageless_defconfig``). Three commands cover
the "what is this guest doing" question:

* ``SysRq-t`` dumps a backtrace for **every** task, in any state.
* ``SysRq-w`` dumps only the **blocked** tasks, those in uninterruptible
  (``D``) sleep.
* ``SysRq-l`` dumps the on-CPU stack of every CPU.

The value of ``SysRq-t`` and ``SysRq-w`` is that they fire on demand and see
tasks in *any* state. That is the difference from the automatic ``hung_task``
detector below, which only reports ``D``-state tasks and only after a timeout
elapses. When a guest is wedged now and you want the stack now, SysRq is the
tool.

Why a console does not help
===========================

On a physical machine you would raise SysRq from the keyboard, with the Alt +
SysRq + key chord, or over a serial console by sending a serial ``BREAK``
followed by the key. A running kdevops guest offers neither path. It runs as
the ``qemu-system@<vm>.service`` systemd service unit under the per-user
service manager, whose ``ttyS0`` serial console is wired to captured stdio in
the journal with no terminal attached, and a persistent service cannot have a
terminal attached to it after the fact.

A kdevops guest does expose interactive consoles, just not ones that carry a
SysRq into this case. A persistent VM offers the virtconsole socket
(``hvc0``) covered in :doc:`guests`, but that is a virtio console rather than a
serial line, so it has no ``BREAK`` to raise SysRq, and it only comes up once
the guest kernel and a getty are running, which is exactly what an early or
activation hang prevents. Launching the VM transiently with :cmd:`systemd-run`
``--pty`` does forward an interactive ``ttyS0``, but that is a different way to
start a VM, not something you can attach to the service already wedged in front
of you.

Injecting the chord through QEMU instead, onto the guest's emulated keyboard,
sidesteps all of it: it reaches any running VM regardless of how its console is
wired, and works before the guest is far enough along to offer a console at
all.

Injecting it through QMP
========================

Each VM's QEMU exposes a `QMP`_ control socket at
``$XDG_RUNTIME_DIR/qemu-system/<vm>/qmp.sock``. The ``send-key`` command on that
socket presses keys on the guest's *emulated* keyboard, which is exactly the
path a physical SysRq chord would take. Connect with :cmd:`socat`, hand-shake
with ``qmp_capabilities``, then send the Alt + SysRq + ``t`` chord as one
keypress:

.. code-block:: text

   $ printf '%s\n' \
       '{"execute":"qmp_capabilities"}' \
       '{"execute":"send-key","arguments":{"keys":[
        {"type":"qcode","data":"alt"},
        {"type":"qcode","data":"sysrq"},
        {"type":"qcode","data":"t"}]}}' \
       | socat - UNIX-CONNECT:"$XDG_RUNTIME_DIR/qemu-system/<vm>/qmp.sock"

Swap the final ``"data":"t"`` for ``"w"`` or ``"l"`` to issue the other
commands. The SysRq handler lives in the kernel's input subsystem, so it fires
even though the console is the captured serial line; its output goes to the
kernel log and from there into the journal.

The guest needs a keyboard driver
----------------------------------

``send-key`` only reaches the guest if the guest has a driver bound to the
emulated keyboard. QEMU's ``q35`` machine emulates a PS/2 keyboard on the i8042
controller, so the guest kernel must build the i8042 ``serio`` driver and the
AT keyboard driver. A minimal kernel with ``CONFIG_INPUT=y`` alone is not
enough: without these, ``send-key`` presses a keyboard nothing is listening to
and SysRq never triggers. ``imageless_defconfig`` therefore enables

.. code-block:: text

   CONFIG_SERIO=y
   CONFIG_SERIO_I8042=y
   CONFIG_INPUT_KEYBOARD=y
   CONFIG_KEYBOARD_ATKBD=y

If you build a custom config for a guest you intend to debug this way, carry
the same four symbols.

Reading the dump back
=====================

SysRq output lands on ``ttyS0``, which the host captures into the user journal
under the QEMU unit. Read it with :cmd:`journalctl`. The serial stream carries
the console's ANSI escapes and bare carriage returns, so strip those and grep
for the SysRq markers:

.. code-block:: console

   $ journalctl --user-unit 'qemu-system@<vm>.service' --no-pager --all \
       --output=cat \
       | sed --regexp-extended 's/\x1B\[[0-9;?]*[A-Za-z]//g; s/\r/\n/g' \
       | grep --ignore-case --extended-regexp \
           'sysrq: show|task:.*state:|call trace'

One caveat worth knowing: the markers above match SysRq's own headers, not the
VM name. A debug guest is often named for the tree under test (here,
``iomap-fixes``), so a naive grep for that subject string drowns in the VM name
rather than the stack. Match SysRq's structural markers instead.

Worked example: the iomap activation hang
=========================================

This page comes out of a real debugging session, which is what makes it a
useful worked example. A kernel built from an iomap patch series (archived on
`lore`_, message-id ``20260625120803.2462291-1-hch@lst.de``) booted far enough
that the guest's `NixOS`_ stage-2 activation began and the service manager was
alive, but the boot never completed and ``ssh <vm>`` timed out. ``hung_task``
could not surface the cause quickly: the stuck task was in ``D`` state and the
120 second timeout had not yet fired. Issuing ``SysRq-w`` produced the blocked
task at once:

.. code-block:: text

   task:chroot  state:D  pid:441  ppid:1
    __schedule / schedule / io_schedule
    filemap_update_page / filemap_get_pages / filemap_read
    __kernel_read / bprm_execve / do_execveat_common / __x64_sys_execve

The stack reads from the bottom up: a ``chroot`` process is in ``execve``,
loading its target binary, and that ``execve`` is blocked in ``io_schedule``
inside ``filemap_read``, waiting for the executable's pages to be read in. In
the imageless model the binary lives on the ``/nix/store`` share, which is a
`virtio-fs`_ mount, so the wedge is a virtiofs page-in that never completes:
activation cannot run the program it needs, and the boot stalls there. The one
captured stack named both the stuck operation and the filesystem responsible,
which is the whole point of reaching for SysRq.

The full Show State dump
========================

``SysRq-w`` narrowed the output to that one blocked task. ``SysRq-t`` instead
dumps every task, which is what you reach for when you do not yet know which
task is to blame. The capture below is a complete ``SysRq-t`` Show State from
the same hung guest, the 74 task stacks exactly as they landed in the journal
(the wide scheduler-debug tables that follow the task list are trimmed). The
blocked ``chroot`` from the worked example is in there too, in ``D`` state. It
is long, so it is collapsed:

.. dropdown:: Full ``SysRq-t`` Show State dump (74 task stacks)

   .. literalinclude:: sysrq-show-state.txt
      :language: text

The hung_task companion
=======================

SysRq is the manual, any-state probe. Its automatic counterpart is the kernel's
``hung_task`` detector, enabled by ``CONFIG_DETECT_HUNG_TASK=y`` with
``CONFIG_DETECT_HUNG_TASK_BLOCKER=y`` and
``CONFIG_DEFAULT_HUNG_TASK_TIMEOUT=120`` (in ``imageless_defconfig`` and the
``debug/hung-task.config`` fragment). It watches for tasks stuck in ``D`` state
past the timeout and prints their stacks unprompted, which is what eventually
catches a hang nobody is watching. The two are complementary: ``hung_task``
notices a ``D``-state stall on its own after the timeout, while ``SysRq-t`` and
``SysRq-w`` let you dump tasks in any state the moment you suspect a guest is
stuck.

.. _SysRq: https://docs.kernel.org/admin-guide/sysrq.html
.. _QMP: https://www.qemu.org/docs/master/interop/qemu-qmp-ref.html
.. _NixOS: https://nixos.org/
.. _virtio-fs: https://virtio-fs.gitlab.io/
.. _lore: https://lore.kernel.org/all/20260625120803.2462291-1-hch@lst.de
