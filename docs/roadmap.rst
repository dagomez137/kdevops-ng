.. SPDX-License-Identifier: copyleft-next-0.3.1

=======
Roadmap
=======

This page records the direction for kdevops-ng. It is a living list of intended
work grouped by theme, not a set of dated commitments; see
:doc:`getting-started/overview` for the current project status. Each card
carries a status, :bdg-primary:`planned` or :bdg-warning:`research`, and an
entry that extends an existing foundation names it.

Documentation and presentation
==============================

.. grid:: 1 1 2 2
   :gutter: 3

   .. grid-item-card:: Built-with badges

      :bdg-primary:`planned`

      Add "built with" badges for the three core components, `Nix`_,
      `systemd`_, and `Windmill`_, to the landing page or footer.
      `Built With Nix`_ supplies the Nix badge; the others need making.

   .. grid-item-card:: Tagged documentation

      :bdg-primary:`planned`

      Adopt `sphinx-tags`_ so pages can be tagged by area (filesystems,
      memory, observability) with generated tag-index pages for filtering.

   .. grid-item-card:: Architecture diagram

      :bdg-primary:`planned`

      Author a `D2`_ diagram of how Windmill, systemd, and Nix fit together,
      and link Windmill's own `architecture page
      <https://www.windmill.dev/docs/misc/architecture>`_ for the internals.

   .. grid-item-card:: Multi-host worker guide

      :bdg-primary:`planned`

      Document deploying workers across hosts and using worker tags to route a
      flow to specific hardware. Extends the worker model in
      :doc:`deployment/nix`.

   .. grid-item-card:: Build-identity explainer

      :bdg-primary:`planned`

      Explain how a :term:`Build identity` keys ``/nix/store``, on one host and
      across peers. Documents the mechanism behind ADR 0002 and the build
      Store.

Usability
=========

.. grid:: 1 1 2 2
   :gutter: 3

   .. grid-item-card:: Opinionated defaults

      :bdg-primary:`planned`

      Make flows and scripts zero-config on the common path and gate the rest
      behind "Advanced options". A kernel build should take a ref and a series
      and infer the rest (checkout, push refs, targets, configuration). Extends
      the "curated forms, not empty boxes" principle.

Cloud and infrastructure
========================

.. grid:: 1 1 2 2
   :gutter: 3

   .. grid-item-card:: Cloud provisioning

      :bdg-primary:`planned`

      Provision a host or a fleet through `Terraform`_ or `OpenTofu`_, the same
      way a local deployment is stood up.

Build identity and artifact storage
===================================

.. grid:: 1 1 2 2
   :gutter: 3

   .. grid-item-card:: Nix-store result archival

      :bdg-warning:`research`

      Today kdevops archives results in `kdevops-results-archive`_, a git
      repository of ``*.xz`` tarballs stored with `git LFS`_ so a user fetches
      only the results they want. It rotates on epochs (the old repository is
      renamed and a fresh one started) to bound git size and keep the public
      `kdevops.org dashboard`_, generated from the archive, fast; it also ships
      ``compare-results-fstests.py`` to diff two runs for regressions and fixes.

      The question is whether to content-address results in the Nix store,
      keyed by their :term:`Build identity` the way builds are, and distribute
      that store over git with the `git-backed Nix store`_ approach, reusing
      the epoch rotation to work around git's storage limits. Open: whether it
      beats git-LFS tarballs, and how it feeds the per-run results summary a
      kdevops-ng user gets in Windmill (the fstests ``report`` verdict, not the
      public kdevops.org dashboard) and the compare tooling.

   .. grid-item-card:: On-demand comparison reports

      :bdg-warning:`research`

      A follow-up to result archival: once results are content-addressed in
      the Nix store by build identity, a report becomes a query over stored
      results rather than a fresh run. Pick any archived results (A, B, C, D),
      each a kernel tested under a given profile, and regenerate a comparison
      on demand: an A/B regression-and-fix diff, or the evolution across
      vanilla releases (7.0, 7.1, and so on) plotted over time.

      Because each result is stored and addressed independently, a baseline
      and a development run need not happen together or in parallel. A vanilla
      baseline tested once is reused months later against a freshly archived
      result, so a baseline that already exists for the profile a user needs
      is never re-run. Git distributes the result store; Windmill steps consume
      it through Nix to assemble the report.

Test coverage
=============

These migrate proven coverage from kdevops into the flow model.

.. grid:: 1 1 2 2
   :gutter: 3

   .. grid-item-card:: More test suites

      :bdg-primary:`planned`

      Port blktests, mmtests, and kselftest groups (xarray, maple tree,
      modules, and others).

   .. grid-item-card:: More filesystems in the fstests flow

      :bdg-primary:`planned`

      Add btrfs, ext4, tmpfs, and other filesystem types alongside XFS.

   .. grid-item-card:: Benchmark suites

      :bdg-primary:`planned`

      Port the benchmarking workflows: sysbench against MySQL and PostgreSQL,
      the fio-based tests, and others.

Developer workflows
===================

.. grid:: 1 1 2 2
   :gutter: 3

   .. grid-item-card:: git-bisect flow

      :bdg-primary:`planned`

      Add a flow that drives ``git bisect`` to track a regression down to the
      commit that introduced it.

Observability and metrics
=========================

.. grid:: 1 1 2 2
   :gutter: 3

   .. grid-item-card:: Guest monitoring

      :bdg-primary:`planned`

      Port guest monitoring for both post-run analysis (recorded) and live
      streaming during a run.

   .. grid-item-card:: Visualization choice

      :bdg-warning:`research`

      Evaluate `Grafana`_ against `Perfetto`_ for presenting captured metrics.

   .. grid-item-card:: Grafana-ready metrics controller

      :bdg-primary:`planned`

      Capture live-stream metrics in a Grafana-ready form: generic system
      metrics (journald guest logs, CPU, RAM); storage metrics (blktrace,
      blkalgn histograms); memory metrics (heatmaps, buddy-allocator status);
      and flamegraphs.

.. _Nix: https://nixos.org/
.. _Built With Nix: https://builtwithnix.org/
.. _systemd: https://systemd.io/
.. _Windmill: https://www.windmill.dev/
.. _sphinx-tags: https://sphinx-tags.readthedocs.io/
.. _D2: https://d2lang.com/
.. _Terraform: https://www.terraform.io/
.. _OpenTofu: https://opentofu.org/
.. _Grafana: https://grafana.com/
.. _Perfetto: https://perfetto.dev/
.. _kdevops-results-archive: https://github.com/linux-kdevops/kdevops-results-archive
.. _git LFS: https://git-lfs.com/
.. _kdevops.org dashboard: https://kdevops.org
.. _git-backed Nix store: https://gist.github.com/wmertens/eceebe0fc05461ebdc8fb106d90a6871
