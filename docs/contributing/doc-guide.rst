.. SPDX-License-Identifier: copyleft-next-0.3.1

==========================
How to write documentation
==========================

This site is written in `reStructuredText`_, built with `Sphinx`_, and styled
with the `PyData Sphinx Theme`_. The conventions below follow the Linux
kernel's documentation guidelines so the two read alike.

.. _Sphinx: https://www.sphinx-doc.org/
.. _reStructuredText: https://docutils.sourceforge.io/rst.html
.. _PyData Sphinx Theme: https://pydata-sphinx-theme.readthedocs.io/

License header
==============

Start every page with an SPDX license identifier on the first line, written as
a reStructuredText comment, followed by a blank line and the title:

.. code-block:: rst

   .. SPDX-License-Identifier: copyleft-next-0.3.1

   ==========
   Page title
   ==========

``scripts/check-style.sh`` checks that every ``.rst`` file under ``docs/``
carries it.

Line length
===========

Wrap prose at 80 columns. Lines that contain a URL are exempt, because a URL
cannot be broken; table rows are exempt for the same reason. Keep code blocks
short, and prefer a continuation over a single long line.

``scripts/check-style.sh`` enforces the limit for ``.rst`` files under
``docs/`` and skips any line that contains a URL.

Headings
========

Use this order of heading adornments, the same as the kernel:

* ``=`` with an overline for the document title.
* ``=`` for chapters.
* ``-`` for sections.
* ``~`` for subsections.

Keeping the higher levels consistent across pages makes the documents easier
to follow.

Use sentence case for heading text: capitalize the first word and any proper
nouns or acronyms, nothing else. Keep upstream spellings such as QEMU, NVMe,
VFIO, IOMMU, SSH, QMP, Nix and NixOS capitalized wherever they appear,
including in headings.

Markup
======

Keep the markup simple; the source should read as plain text. Use ``::`` for a
plain fixed-width block, ``.. code-block:: <language>`` for a block that
benefits from highlighting, and double backticks for inline literals.

Links
=====

For a link you reuse, or to keep a long URL out of the prose, define a named
target and reference it by name, the way this page links `Sphinx`_ and
`reStructuredText`_. Keep the target definition next to the paragraph that
uses it:

.. code-block:: rst

   The site is built with `Sphinx`_.

   .. _Sphinx: https://www.sphinx-doc.org/

For a one-off link, an inline link is fine:

.. code-block:: rst

   See `git-worktree(1) <https://git-scm.com/docs/git-worktree>`__.

Link to another page of this site with ``:doc:`` and to a labelled section
with ``:ref:`` rather than hard-coding a path.

Editor setup
============

The repository ships an ``.editorconfig`` that sets the 80 column limit for
``.rst`` files, and a Helix configuration (``.helix/languages.toml``) that
sets ``text-width`` and a ruler at column 80. In Helix, reflow a paragraph to
the limit with ``:reflow``.
