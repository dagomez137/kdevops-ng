.. SPDX-License-Identifier: copyleft-next-0.3.1

==================
Designing a form
==================

A flow's form is how a kernel developer drives kdevops-ng, so it carries the
knowledge they already have as named choices instead of asking them to type
it. This is a design convention, not one of the :doc:`/concepts/principles`: a
form that ignores it still runs correctly; it is only harder to use.

The convention
==============

- Offer the set developers actually use (kernel trees, maintainers, packages,
  profiles, test suites, kernel parameters) as a checklist, dropdown or picker
  with human labels instead of an empty box, and feature the common picks as
  defaults, so the zero-configuration run is already useful.
- Keep each list once, in the step code, and have every step and flow that
  needs it read it from there. A missing choice is added to that list, not typed
  into a free-form field.
- A picker that reads live state (the guests, the Bare's refs, a guest's
  discovered suites) falls back to the curated set before that state exists,
  so it is never empty.
- Where the long tail needs raw configuration, offer it as an advanced
  override that says so in its title or description and replaces the curated
  input when set. It is never the primary input.

Two worked examples
===================

The workbench mirror form, where the convention started: a checklist of
kernel.org trees from ``KERNEL_TREES`` in :src:`f/workbench/fetch`, the
one list both the Bare and the mirror timers are built from.

The closure form's package sources, in :src:`f/nix/build`: one labelled Git
Ref picker per overridable package, each fed by its project's Bare. Any other
package goes through ``extra_overrides`` on
:src:`render_config <f/nix/render_config>`, the labelled raw escape.

What the test checks
====================

``tests/test_curated_forms.py`` refuses an unmarked raw box in any flow form;
its docstring says what marks one. Whether a list is the one developers use is
left to review.
