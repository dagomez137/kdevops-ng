.. SPDX-License-Identifier: copyleft-next-0.3.1

=======
Staging
=======

Staging holds work that is new and not yet proven: written, but not yet
tested and blessed for production. It is kept apart from the vetted material
until it has been exercised, found correct, and blessed, then promoted. The
project stages work this way in two places, its code and its prose.

New flows and their steps are deployed to a dedicated ``staging`` `Windmill`_
workspace that runs beside the production ``kdevops`` workspace on the same
instance, so a flow can be run against live guests and iterated on before it
is trusted. ``nix run .#deploy-staging`` pushes the whole tree there.

``nix run .#deploy-kdevops`` deploys to production, but first prunes a
staging-only set of paths, so ``kdevops`` carries only production-ready work.
That set is a single list at the top of :src:`nix/apps/default.nix`.
Promoting a flow, once it is tested and blessed, is deleting its entry from
that list: the next ``deploy-kdevops`` no longer holds it back and carries it
to ``kdevops``.

The pages listed below are the prose side: promoted from working notes into
reStructuredText but **not yet reviewed**. They live at the ``docs/`` paths
they will keep, so they render here and on Read the Docs and can be read in
full, but they are flagged ``:orphan:`` and kept out of the section toctrees,
so the rest of the site does not present them as vetted documentation.

To audit one: read it, and if it is correct, delete its ``:orphan:`` line, add
its name to the toctree of the listed section ``index.rst``, and remove its
entry from the list below.

Contributing
============

- :doc:`/contributing/test-suites` (add to ``docs/contributing/index.rst``)
- :doc:`/contributing/testing` (add to ``docs/contributing/index.rst``)

Reference
=========

- :doc:`/reference/openflow` (add to ``docs/reference/index.rst``)
- :doc:`/reference/kernel-toolchains` (add to ``docs/reference/index.rst``)

Concepts
========

- :doc:`/concepts/build-store` (add to ``docs/concepts/index.rst``)
- :doc:`/concepts/cross-host-development` (add to ``docs/concepts/index.rst``)

Deployment
==========

- :doc:`/deployment/monitoring` (add to ``docs/deployment/index.rst``)

Flows
=====

- :doc:`/flows/kernel-build` (add to ``docs/flows/index.rst``)
- :doc:`/flows/kunit` (add to ``docs/flows/index.rst``)
- :doc:`/flows/nix-build` (add to ``docs/flows/index.rst``)
- :doc:`/flows/qemu-build` (add to ``docs/flows/index.rst``)
- :doc:`/flows/nvme-testing` (add to ``docs/flows/index.rst``)
- :doc:`/flows/selftests` (add to ``docs/flows/index.rst``)
- :doc:`/flows/runtime-tests` (add to ``docs/flows/index.rst``)
- :doc:`/flows/usertests` (add to ``docs/flows/index.rst``)
- :doc:`/flows/sysrq` (add to ``docs/flows/index.rst``)
- :doc:`/flows/bisect` (add to ``docs/flows/index.rst``)
- :doc:`/flows/boot` (add to ``docs/flows/index.rst``)
- :doc:`/flows/bringup` (add to ``docs/flows/index.rst``)
- :doc:`/flows/workbench-init` (add to ``docs/flows/index.rst``)
- :doc:`/flows/blktests` (add to ``docs/flows/index.rst``)

.. _Windmill: https://www.windmill.dev/
