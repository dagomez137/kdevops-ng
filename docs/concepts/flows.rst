.. SPDX-License-Identifier: copyleft-next-0.3.1

===============
Flows and steps
===============

The `Windmill`_ workspace content lives under ``f/``, managed by ``wmill``. Its
names follow a convention so the tree reads as a set of actions grouped by
concern. Each subsystem directory under ``f/`` groups one concern, for
example :src:`f/kernel`, :src:`f/qemu` or :src:`f/qsu`.

::

   f/kernel/
   ├── build.flow             a flow (composes steps)
   ├── configure.py           a step (imperative verb)
   ├── prepare_worktree.py    a step (verb_object)
   ├── reuse_check.py
   └── identity.py            a shared module (noun)

These are Windmill concepts. A flow is a directed graph of steps; both are
stored in the `OpenFlow specification`_, the open format the ``.flow`` files
use. See the `flow editor`_ for the engine's own documentation.

.. _Windmill: https://www.windmill.dev/
.. _OpenFlow specification: https://www.windmill.dev/docs/openflow
.. _flow editor: https://www.windmill.dev/docs/flows/flow_editor

Steps
=====

A step is a script named for the action it performs, in the imperative mood,
such as ``build``, ``configure``, ``compile``, ``install``, ``boot``,
``publish`` or ``fetch``. When the action takes an object, the name is
``verb_object`` in snake_case, such as ``prepare_worktree``,
``fetch_identity`` or ``install_modules``. Keep one step per concern so each
step is independently testable.

A step can be written in any language Windmill supports (Python, TypeScript,
Bash, Go, Rust and more); the canonical, current list is the ``language``
enum in the `OpenFlow schema source`_. The steps in this workspace are
Python.

.. _OpenFlow schema source:
   https://github.com/windmill-labs/windmill/blob/main/openflow.openapi.yaml

Flows
=====

A flow is a ``<verb>.flow`` that composes steps into a pipeline, such as
``build.flow``, ``boot.flow`` or ``bringup.flow``. Flows stay thin; the
reusable logic lives in the steps they call.

Shared modules
==============

A module that holds shared logic or data, rather than performing one flow
step, takes a noun name, such as ``common.py``, ``identity.py`` or
``worktree.py``.
