.. SPDX-License-Identifier: copyleft-next-0.3.1

=======
Staging
=======

The pages below were promoted from working notes into reStructuredText but have
**not yet been reviewed**. They live at the ``docs/`` paths they will keep, so
they render here and on Read the Docs and can be read in full, but they are
flagged ``:orphan:`` and kept out of the section toctrees, so the rest of the
site does not present them as vetted documentation.

To audit one: read it, and if it is correct, delete its ``:orphan:`` line, add
its name to the toctree of the listed section ``index.rst``, and remove its
entry from the list below. When the list is empty, delete this page and its
entry in ``docs/index.rst``.

Reference
=========

- :doc:`/reference/openflow` (add to ``docs/reference/index.rst``)
- :doc:`/reference/kernel-toolchains` (add to ``docs/reference/index.rst``)

Concepts
========

- :doc:`/concepts/build-store` (add to ``docs/concepts/index.rst``)
- :doc:`/concepts/cross-host-development` (add to ``docs/concepts/index.rst``)

Flows
=====

- :doc:`/flows/kernel-build` (add to ``docs/flows/index.rst``)
- :doc:`/flows/nix-build` (add to ``docs/flows/index.rst``)
- :doc:`/flows/qemu-build` (add to ``docs/flows/index.rst``)
- :doc:`/flows/nvme-testing` (add to ``docs/flows/index.rst``)
