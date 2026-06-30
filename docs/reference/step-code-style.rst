.. SPDX-License-Identifier: copyleft-next-0.3.1

===============
Step code style
===============

A Windmill step is a small program in one language. This project keeps that
code as the source of truth in git, so each language it uses needs a named,
enforced style: the standard it follows, the tool that checks it, and the
Windmill rules that make step code different from ordinary scripts.

Python is the only language in the workspace today, so it is specified in
full below. The structure is deliberately per-language: a new language gets
its own section with the same four parts (baseline standard, tools and how to
run them, formatting and line length, and any Windmill-specific contract).
See `Other languages`_ for the set Windmill supports and how a new one is
added here.

The Windmill ``main()`` contract
================================

Across every language, a step's entry point is a function named ``main`` (a
`Windmill script entrypoint`_). Its parameters are not just arguments: their
type annotations are the workspace form schema. Windmill parses the ``main``
signature statically (it does not run the module) and turns each annotated
parameter into a property of a `JSON Schema`_ that Windmill `infers from the
signature`_, which renders as a UI form field. A parameter with a default
value, or an optional type, becomes a non-required field.

This is semantic typing. A linter or type checker must not "simplify" these
annotations, and ``main`` may legitimately take domain-shaped values and
return a ``dict``-shaped payload. The relaxations described under
`Type checking`_ exist for exactly this reason.

.. _Windmill script entrypoint: https://www.windmill.dev/docs/getting_started/scripts_quickstart/python#code
.. _JSON Schema: https://json-schema.org/overview/what-is-jsonschema
.. _infers from the signature: https://www.windmill.dev/docs/core_concepts/json_schema_and_parsing

Python
======

Python is used in two places, both governed by one ``pyproject.toml`` at the
repository root:

- ``scripts/*.py``: repository tooling (``gen-bringup.py``,
  ``reflow-descriptions.py``), plain CPython plus PyYAML.
- ``f/**/*.py``: the hand-authored Windmill step scripts.

The target runtime is CPython 3.11. Ruff is pinned to it through
``target-version``; Pyright is not version-pinned and infers 3.11 from the
devshell interpreter. Either way the modern built-in generics and union syntax
are available everywhere.

Standards baseline
------------------

The baseline is :pep:`8`, but only the parts the enabled Ruff rule families
actually enforce are gated; the rest is convention upheld in review. The
enabled families are ``E``, ``F``, ``I``, ``UP``, and ``B`` (resolve any prefix
in the `Ruff rules index`_):

- ``E`` (pycodestyle): the mechanical layout rules (whitespace, blank lines,
  indentation). The one exception is ``E501`` (line length), which is
  disabled; the formatter owns wrapping instead (see `Line length`_).
- ``F`` (pyflakes): correctness defects such as undefined names, unused
  imports, and unused locals. This is logic level, not style.
- ``I`` (isort): import ordering and grouping (see `Imports`_).
- ``UP`` (pyupgrade): rewrites legacy syntax to the modern py311 idiom, such as
  the built-in generics and the ``X | None`` union; the tree already follows
  it, so this locks in a rule rather than introducing one.
- ``B`` (flake8-bugbear): likely-bug patterns, such as a mutable default
  argument or an ``except`` clause that re-raises without ``from``.

Not enabled, and therefore not gated (they are upheld in review): ``D``
(docstrings), ``N`` (naming), and ``RUF``. In particular the step naming rule
(``verb_object`` snake_case for steps, nouns for libraries) is a human
convention, not a tool check.

.. _Ruff rules index: https://docs.astral.sh/ruff/rules/

Tools
-----

All Python configuration lives in ``pyproject.toml``.

Ruff
   The single linter and formatter. It lints ``E``, ``F``, ``I``, ``UP``, and
   ``B`` (with ``E501`` ignored) and formats in the `Black-compatible style`_ at
   a line length of 88. ``target-version`` is ``py311`` and ``vendor`` is
   excluded. This is the gate.

Pyright
   Type checks in ``basic`` mode. It is advisory only and is not part of the
   gate. It includes ``scripts`` and ``f``, resolves the repository root as an
   extra path, and relaxes several diagnostics, some only for ``f/`` and some
   globally (see `Type checking`_). The mode and every ``report*`` rule name are
   documented in the `Pyright configuration`_.

.. _Black-compatible style: https://docs.astral.sh/ruff/formatter/#black-compatibility
.. _Pyright configuration: https://microsoft.github.io/pyright/#/configuration

How to run them:

.. code-block:: console

   $ nix flake check                           # lint and format check
   $ nix run .#format                          # apply lint and format fixes
   $ nix develop .#checks --command pyright     # advisory type check

Line length
-----------

The line length is 88 columns, the `Ruff line-length default`_, not
:pep:`8`'s 79. The wider limit reduces wrapping while staying readable, which
is `Black's rationale`_ for choosing 88. Because Ruff's formatter is the single
authority on wrapping, the ``E501`` lint is disabled: it disagrees with the
formatter on the comments, strings, and URLs the formatter leaves unwrapped by
design, so keeping it on would mean fighting the formatter.

.. _Ruff line-length default: https://docs.astral.sh/ruff/settings/#line-length
.. _Black's rationale: https://black.readthedocs.io/en/stable/the_black_code_style/current_style.html#line-length

Imports
-------

Import ordering is enforced by Ruff's ``I`` (isort) rules and follows the
:pep:`8` grouping: standard library first, then third party, then first party,
each group sorted and separated by a blank line. In step scripts the
first-party group is the sibling imports, written as ``from f.x import y``
(there is no ``__init__.py``; ``f`` resolves as a namespace package from the
repository root).

.. code-block:: python

   from __future__ import annotations

   import json
   import os
   from pathlib import Path

   from f.common.devshell import Nix

The ``from __future__ import annotations`` line (:pep:`563`) keeps annotations
as strings, so they are never evaluated at run time and forward references need
no quoting. Windmill parses the signature statically either way, so this is
ordinary runtime hygiene rather than a schema requirement.

Typing
------

Typing follows :pep:`484` with the modern syntax from :pep:`585` (built-in
generics such as ``list[str]`` and ``dict[str, int]``) and :pep:`604` (the
``X | None`` union). Because the target is 3.11, these need no
``from typing import`` and are preferred over the older ``List``, ``Dict``,
and ``Optional`` spellings.

In a step, the ``main`` annotations are the form schema, so they carry extra
meaning. Windmill maps Python types to form fields as follows:

- ``str``, ``int``, ``float``, ``bool``: text, integer, number, and checkbox.
- ``list[T]``: an array field with typed items. ``dict``: a JSON object field.
- ``bytes``: a base64 string. ``datetime`` and ``date``: date-time and date
  pickers.
- A default value, ``Optional[T]``, or ``T | None``: a non-required field.
  Everything else is required.
- ``Literal[...]`` and a string ``Enum``: a select with those choices.
- An unrecognised annotation name: a typed resource picker (its type is the
  name). ``S3Object`` is the ``s3_object`` resource.

A library module (a noun such as ``common.py`` or ``devshell.py``) has no
``main`` and is imported with ``from f.x import y``.

A step's ``main`` therefore looks like this, with annotated scalars and a
``dict`` return:

.. code-block:: python

   def main(
       worktree: str,
       build_dir: str,
       targets: str = "",
       make_flags: str = "",
   ) -> dict:
       ...

Here ``targets`` and ``make_flags`` have defaults, so they render as optional
fields; ``worktree`` and ``build_dir`` are required.

Type checking
-------------

Pyright is advisory, not a gate, and two kinds of relaxation apply. The first
is scoped to ``f/`` through a Pyright execution environment: a step's ``main``
annotations are a form schema, not ordinary typing, and it returns a
``dict``-shaped payload whose keys are read dynamically, so
``reportArgumentType`` and ``reportAttributeAccessIssue`` are turned off under
``f``. The second is
global: both trees import ``f`` siblings as namespace packages over the repo
root (there is no ``__init__.py``; even ``scripts/gen-bringup.py`` does
``from f.qsu.binaries import ...``), and the ``wmill`` client is injected at run
time, so ``reportMissingImports`` and ``reportMissingModuleSource`` are set to
``none`` at the top level to avoid false unresolved-import noise. Apart from
those two import diagnostics, ``scripts/`` keeps the basic default.

Docstrings
----------

A step opens with a module docstring in the :src:`f/kernel` and :src:`f/qemu`
style:
a short prose summary followed by an ``Equivalent command`` (or
``Equivalent bash``) block that shows the operation as a copy-pasteable shell
command. There is no docstring rule in the gate, and none is wanted: requiring
a docstring on every symbol would force boilerplate that restates the obvious.

Naming
------

A step file is named for the action it performs, in the imperative mood, in
``verb_object`` snake_case when it takes an object (``prepare_worktree``,
``install_modules``). A library or data module is a noun (``common.py``,
``identity.py``). Field labels use the upstream spelling and a schema
``title:`` overrides Windmill's auto-title-casing for acronyms
(``qemu_binary`` becomes ``QEMU Binary``); the ``title`` key is one of
Windmill's `advanced schema settings`_. These are conventions upheld in
review, not tool checks.

.. _advanced schema settings: https://www.windmill.dev/docs/core_concepts/json_schema_and_parsing#advanced-settings

Other languages
===============

Windmill runs step code in many languages. The current supported set is
Python 3, TypeScript (in three flavors: Bun, Deno, and the native-worker
variant ``nativets``), Go, Bash, PowerShell, PHP, Rust, C#, Java, Ruby,
Nushell, R, Ansible, GraphQL, and the SQL dialects PostgreSQL, MySQL, MS SQL,
BigQuery, Snowflake, DuckDB, and Oracle. Windmill chooses the language from the
file extension on sync.

When this workspace adds code in any of these, that language gets its own
section here, structured like `Python`_: its standard baseline, its tools and
how to run them, its formatting and line length, and the Windmill ``main()``
contract restated in that language's terms. The near-term candidate is
TypeScript: ``wmill.yaml`` sets ``defaultTs: bun``, so a bare ``.ts`` step
resolves to Bun.
