.. SPDX-License-Identifier: copyleft-next-0.3.1

===========================================
Writing commit messages, docs and comments
===========================================

kdevops-ng is reviewed the way the kernel is, so its prose follows how the
kernel's own maintainers write. The rules below are the ones the kernel's
process documents state, and the numbers come from a sample of the 200 latest
non-merge commits by each of 15 maintainers at Linux v7.3-rc7: Linus Torvalds,
Greg Kroah-Hartman, Thomas Gleixner, Andrew Morton, Ingo Molnar, Christoph
Hellwig, Jens Axboe, Darrick J. Wong, Dave Chinner, Theodore Ts'o, Kees Cook,
Jonathan Corbet, Paolo Bonzini, Jakub Kicinski and Al Viro.

Commit messages
===============

The project's own form is in the commit rules of ``CLAUDE.md``. What goes into
the body comes from `submitting-patches.rst`_ and the `tip tree`_ handbook:

- Lead with the problem, then its effect a user would see, then the change in
  plain words. 86% of the sampled bodies open with context, not with the change.
- Say why, for a reader years later. The body should make sense to someone who
  never saw the discussion. Give numbers for a performance or size claim, and
  name what the change costs.
- Write in the imperative and do not narrate the patch. Give orders to the code
  base ("make xyzzy do frotz"); never open with the patch or the commit as the
  subject (maintainers do, in 0.2% of commits), and never retell the diff. Code
  is not a character either: "Cancel the worker" rather than "we make the
  handler cancel the worker".
- Keep it short. The median body is 6 lines (46 words), the upper quartile 12,
  and only 5% use a list. One problem per commit; a long body is a sign the
  commit should be split.
- Cite a commit by its first 12 characters and its subject, and summarise a
  discussion rather than only linking it. Trim a log or a backtrace to the lines
  that show the problem.
- Name functions with parentheses, ``foo()``, in the subject and the body.

Examples
--------

Read two before writing, with ``git -C <linux> show --no-patch <hash>`` in any
Linux clone, the Workbench's mirror included; a list of banned words on its own
mostly teaches a writer to reach for a synonym.

``43a1e3744548`` (Linus Torvalds)
   A bug fix: the problem, why the obvious code missed it, the chosen fix and
   the alternative it rejects.
``0e65cd9e5d41`` (Paolo Bonzini)
   A preventive fix that says plainly nothing was observed, and why it is
   still right.
``c793bbfc4a0a`` (Thomas Gleixner)
   Context, problem and fix in three short paragraphs, the tip tree's order.
``a92193c91e88`` (Jens Axboe)
   A regression fix whose evidence is a trimmed KASAN report.
``ab1c416d2377`` (Darrick J. Wong)
   Names the tool that found the bug, and why the simple fix is enough.
``c01e6df60e7b`` (Christoph Hellwig)
   A feature: why it is useful, then its one design decision and the
   alternative.
``12c84dd4d308`` (Theodore Ts'o)
   A feature in four lines.
``3ed403bbc967`` (Thomas Gleixner)
   A cleanup in two sentences of reason and one order.
``f2e65e4e5b4b`` (Jonathan Corbet)
   A documentation fix: what the text claimed, why that is wrong, the change.
``a5beeb64f226`` (Al Viro)
   A comment-only commit whose body explains the locking it documents.
``ac059ae422d7`` (Ingo Molnar)
   A build fix with the compiler error and the bisect result, ending "Build
   tested only".
``9db03d2116ab`` (Jens Axboe)
   A revert that admits the wrong assumption and names the user it broke.
``761e015e5a54`` (Christoph Hellwig)
   A performance fix that says what must stay and which regression it ends.
``d681d7ef617e`` (Linus Torvalds)
   A merge: why it is merged now.

Comments and docstrings
=======================

From the commenting sections of `coding-style.rst`_, the `tip tree`_ handbook
and the `KVM x86`_ handbook:

- Say what and why, never how. Write the code so that how it works is plain; a
  comment that repeats it is noise, and a comment cannot rescue code that is
  hard to read.
- Comment what would tempt a wrong cleanup: a barrier, a lock rule, an ordering
  that looks arbitrary. An assertion beats a comment where one can say it.
- Do not document the obvious. No comment that only names the next line's
  operation, no docstring that repeats the signature, and no history ("added",
  "fixed"): history belongs in git.
- Write in the imperative, without pronouns, with sentences that start with a
  capital letter.

``CLAUDE.md`` (Conventions) puts the reason in the commit body, not the
comment.

Documentation
=============

From the kernel's `doc guide`_: keep reStructuredText markup simple, update the
content and not only the formatting, and write for every reader, including one
who arrives from a search with no context. This repository's page rules (80
columns, first-mention links, host and guest blocks) are in :doc:`doc-guide`.

The gate
========

``scripts/check-prose.py`` refuses the phrasing these maintainers almost never
write. ``check-style.sh`` runs it on the message alone in the commit-msg hook,
and otherwise (run directly, or per commit by ``nix run .#check``) on the
message plus the lines a commit adds to ``.rst`` and ``.md`` files and to the
comments and docstrings of ``.py``, ``.sh`` and ``.nix`` files. ``vendor/``
keeps its own conventions. To check whole files outside a commit, such as a new
page before review, run ``python3 scripts/check-prose.py --file <path>...``. The
exact patterns live in the script; by kind they are:

- Refused: the vocabulary of generated text, the patch or commit as the subject
  of a sentence, en and em dashes, filler openers and summary headings, a list
  in a commit body, and, in comments, history words and lines that only name an
  increment or decrement.
- Warned: inflated words, connective openers, padding such as a long way of
  saying "to", and, in comments, openers that restate the code or describe "this
  function" or a "helper". A warning prints and passes; a maintainer writes
  these now and then.

Each refused message pattern but the list rule, which comes from ``CLAUDE.md``,
was measured before it went in. Over three years, a second sample of 7,742
commit messages by these maintainers matches 14 times, 0.18%, and 13 of those
are the patch-as-subject opener; the vocabulary list matches none. The rest of
the kernel matches 2.4%. The dash alone shows the difference: 1.3 in 10,000
maintainer messages, 199 in 10,000 commits that carry ``Assisted-by``.

Some words stay off the list on purpose: robust, utilize, various and properly
appear in maintainer commits as often as anywhere else, so refusing them would
refuse good writing.

What a pattern cannot judge
---------------------------

A pattern cannot tell whether a comment restates its code or whether a body
explains the problem before the fix; that takes a reader. A writer reviewing
their own draft cuts less than a second reader does, so for anything longer than
a few lines, give the draft to a reader who did not write it: a colleague, or a
model in a fresh context. Hand it this page, the draft, the diff, and this
brief:

.. code-block:: text

   You are the editor, not the author. Read docs/contributing/writing.rst
   and two of its example commits. For the draft below, list every sentence
   that restates the diff, narrates the patch, explains how rather than why,
   or could be deleted without loss, then give the shortest rewrite that
   keeps every fact a reviewer needs. Keep technical terms, numbers and
   commit references exactly. Do not add anything.

.. _submitting-patches.rst: https://docs.kernel.org/process/submitting-patches.html
.. _tip tree: https://docs.kernel.org/process/maintainer-tip.html
.. _coding-style.rst: https://docs.kernel.org/process/coding-style.html
.. _KVM x86: https://docs.kernel.org/process/maintainer-kvm-x86.html
.. _doc guide: https://docs.kernel.org/doc-guide/sphinx.html
