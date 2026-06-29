.. SPDX-License-Identifier: copyleft-next-0.3.1

:orphan:

=====================
OpenFlow object model
=====================

OpenFlow is the object model Windmill uses to describe a flow: the steps, how
they are wired together, and the controls (loops, branches, approvals, retries)
that surround them. This page is the authoritative reference for the model as
this project uses it. It is distilled from the upstream OpenFlow schema and the
Windmill worker source, not from the public documentation page, which is
simplified and lists fewer module types and languages than the real model. The
real model has nine module types and supports many languages; the durable
details below come straight from the upstream source.

A flow lives in a ``.flow/flow.yaml`` file under ``f/`` and is stored in the
canonical workspace YAML form (see :doc:`/reference/wmill-yaml`).

Object model
============

A flow is an ``OpenFlow`` object. Its top-level fields carry the metadata and
inputs; its ``value`` is a ``FlowValue`` that holds the steps and the
flow-wide controls::

   OpenFlow                    # a .flow/flow.yaml file
   ├─ summary       string     # plain text heading (not Markdown-rendered)
   ├─ description   string     # GitHub-Flavored Markdown on the detail page
   ├─ schema        JSONSchema # the flow's inputs (properties/order/required)
   └─ value: FlowValue
      ├─ modules: FlowModule[] # the steps, run in DAG order
      ├─ failure_module        # runs on flow failure; id must be "failure"
      ├─ preprocessor_module   # runs first on a trigger; id "preprocessor"
      ├─ same_worker     bool  # pin all steps to one worker + share ./shared
      ├─ preserve_step_tags    # let steps keep their tag under a flow tag
      ├─ concurrent_limit / concurrency_key / concurrency_time_window_s
      ├─ debounce_* / max_total_debounc*
      ├─ skip_expr       string # JS expr to skip the whole flow
      ├─ early_return    string # JS expr to return early
      ├─ cache_ttl       number # cache flow results (seconds)
      ├─ flow_env        object # env for all steps; "$var:path"/"$res:path"
      └─ priority        number # higher runs first

Each entry in ``modules`` is a ``FlowModule``: one step plus the per-step
controls that decide whether, when, and how it runs::

   FlowModule
   ├─ id            string  # reference its result as results.<id>; in paths
   ├─ value: FlowModuleValue # one of the nine types below (by .type)
   ├─ summary       string
   ├─ skip_if       {expr}  # JS -> true skips this step
   ├─ stop_after_if {expr, skip_if_stopped, error_message}
   ├─ stop_after_all_iters_if # loops only: stop after all iterations
   ├─ sleep         InputTransform # delay before running (seconds or expr)
   ├─ cache_ttl     number
   ├─ timeout       InputTransform # max seconds (static or expr)
   ├─ mock          {enabled, return_value} # return mock instead of running
   ├─ suspend       {...}   # approval / resume step (see below)
   ├─ retry: Retry          # constant / exponential / retry_if
   ├─ continue_on_error bool # flow continues even if this step fails
   └─ priority      number

The ``id`` matters beyond observability: a step's result is referenced as
``results.<id>`` in later transforms, and the id appears in worktree paths.

Module types
============

The ``value.type`` of a ``FlowModule`` selects one of nine kinds:

.. list-table::
   :header-rows: 1
   :widths: 22 20 58

   * - ``type``
     - Schema
     - Use
   * - ``rawscript``
     - RawScript
     - Inline code (our default; the bash lives here).
   * - ``script``
     - PathScript
     - Call a saved script by ``path`` (plus optional ``hash``,
       ``tag_override``).
   * - ``flow``
     - PathFlow
     - Call another flow as a subflow by ``path``.
   * - ``forloopflow``
     - ForloopFlow
     - Iterate over an array; supports ``parallel`` and ``parallelism``.
   * - ``whileloopflow``
     - WhileloopFlow
     - Loop while a condition holds (use ``stop_after_if``).
   * - ``branchone``
     - BranchOne
     - First matching ``expr`` branch runs, else ``default``.
   * - ``branchall``
     - BranchAll
     - All branches run (``parallel`` optional); per-branch ``skip_failure``.
   * - ``identity``
     - Identity
     - Pass-through (placeholder or debug).
   * - ``aiagent``
     - AiAgent
     - Tool-calling LLM step (provider, tools, ``output_schema``).

RawScript: the inline default
-----------------------------

A ``rawscript`` carries the step's source inline. It is the kind this project
uses for hand-authored steps:

.. code-block:: yaml

   value:
     type: rawscript
     language: bash         # deno bun python3 go bash powershell and more
     content: |             # the source; for non-bash, export a main(...)
       ...
     input_transforms: {}   # maps each arg name -> static/javascript
     # optional per-step controls:
     tag: <worker-group>            # route this step to a worker group
     concurrent_limit / concurrency_time_window_s / custom_concurrency_key
     lock: <deps lockfile>
     assets: [{path, kind: s3object|resource|ducklake}]

Windmill supports many ``language`` values beyond bash (``deno``, ``bun``,
``python3``, ``go``, ``powershell``, the SQL dialects, ``rust``, ``php``,
``java``, and more); for every non-bash language the content must export a
``main(...)`` entry point.

PathScript and PathFlow: composition
------------------------------------

A ``script`` module reuses a deployed script by ``path`` (with an optional
``hash`` to pin a version, and ``tag_override`` to re-route the worker group).
A ``flow`` module runs a deployed flow as a subflow. Both take
``input_transforms``. This is how the project composes work: small reusable
scripts (``f/kernel/*``, ``f/qemu/*``) chained by thin orchestrator flows.

Loops and branches
-------------------

``forloopflow`` takes an ``iterator``, a JS expression returning an array.
Inside the loop body, ``flow_input.iter.value`` and ``flow_input.iter.index``
give the current element and position. Set ``parallel: true`` with
``parallelism: N`` to run N iterations concurrently. ``skip_failures: true``
lets a failed iteration return ``null`` instead of aborting the loop.

``whileloopflow`` repeats its ``modules`` until a ``stop_after_if`` fires.

``branchone`` evaluates its branches in order; the first whose ``expr`` is true
wins, and ``default`` runs if none match. ``branchall`` runs every branch (set
``parallel`` to run them concurrently), with a per-branch ``skip_failure``.

Wiring data with input_transforms
=================================

Each key in a step's ``input_transforms`` is an argument name of the step's
code. Its value is one of two shapes:

- ``{type: static, value: <any>}``: a constant. Use a ``$res:path`` reference
  for a resource value.
- ``{type: javascript, expr: <JS>}``: an expression evaluated at runtime.

The expression can read these variables:

- ``flow_input.<prop>``: the flow's inputs, from ``schema.properties``.
- ``results.<step_id>``: a previous step's result.
- ``flow_input.iter.value`` and ``flow_input.iter.index``: inside a forloop.
- ``error`` and ``result``: inside ``retry_if`` and ``stop_after_if``.

A build step wires two flow inputs straight through:

.. code-block:: yaml

   input_transforms:
     git_ref:   { type: javascript, expr: flow_input.git_ref }
     defconfig: { type: javascript, expr: flow_input.defconfig }

Bash specifics
==============

The bash steps this project relies on have two behaviours worth pinning down:
argument inference and result capture.

Argument inference
------------------

The bash parser derives arguments from contiguous top-of-file lines matching
``name="$N"`` or ``name="${N:-default}"``, starting at ``$1``. It stops at the
first gap, so declare ``$1`` through ``$N`` with no holes. Later references
such as ``$SANDBOX`` or ``${WM_ROOT_FLOW_JOB_ID:-...}`` do not create phantom
arguments, because they are not of the ``"$<digit>"`` form. An omitted optional
input can arrive as the literal string ``null``; normalize it.

Result capture
--------------

The worker captures a bash step's result in this priority order:

1. a ``result.json`` file written in the cwd (the job dir), returned as JSON;
2. a ``result.out`` file, returned as a string;
3. otherwise the last line of stdout, trimmed.

This project writes ``result.json`` to emit a structured manifest (the
``bzImage`` path, ``kernelrelease``, the commit, and so on) that downstream
steps consume.

Injected environment
--------------------

The worker injects a set of ``WM_*`` variables: ``WM_JOB_ID`` (this step's
job), ``WM_ROOT_FLOW_JOB_ID`` (stable for the whole flow run, the
custom-worktree job-id fallback), ``WM_FLOW_JOB_ID``, ``WM_FLOW_PATH``,
``WM_WORKSPACE``, ``WM_USERNAME``, and ``WM_PERMISSIONED_AS``. Whitelisted host
environment (``WORKER_INDEX``, ``WORKERS_DIR``, the D-Bus socket variables) is
injected by this project's worker unit.

Passing files between steps
===========================

Step results are JSON, so they cannot carry a file directly. To hand files from
one step to the next, set ``value.same_worker: true`` on the flow and use the
shared directory: the worker keeps the job dir and bind-mounts
``<job_dir>/shared`` at ``/tmp/shared`` (reachable as ``./shared`` from the
step).

This matters because each worker's sandbox (``$WORKERS_DIR/<index>``) is
mounted only into that worker's container, so artifacts a build writes on one
worker are invisible to another. A multi-step pipeline that builds a kernel and
then boots it in QEMU must do one of two things:

- set ``same_worker: true`` so build and boot land on the same worker (then the
  manifest's ``bzImage`` path is reachable, or use ``./shared``); or
- write artifacts to ``$WORKERS_DIR/shared/...`` (mounted read-write in every
  worker) and pass the path through the result manifest.

``same_worker`` is the clean default for a build-then-boot pipeline. For
dedicated VM-lifecycle workers later, give those steps a ``tag`` and run a
worker group that carries that tag.

Approval and suspend steps
==========================

A ``module.suspend`` block turns a step into an approval gate. Its fields are
``required_events`` (the number of approvals needed), ``timeout``,
``resume_form.schema`` (a form to collect input on resume),
``user_auth_required``, ``user_groups_required``, ``self_approval_disabled``,
and ``continue_on_disapprove_timeout``. A run is resumed or cancelled through
the ``/w/<ws>/jobs/resume/<job_id>`` and ``/jobs/cancel/<job_id>`` endpoints.

Retries and error handling
==========================

A step's ``retry`` is either ``retry.constant {attempts, seconds}`` or
``retry.exponential {attempts, multiplier, seconds, random_factor}``. A
``retry.retry_if.expr`` is a JS expression over ``result`` and ``error`` that
decides whether to retry at all.

For failures that should not retry, ``continue_on_error`` on a step lets the
flow proceed past a failed step, and a flow-level ``failure_module`` (its id
must be ``failure``) receives ``{message, name, stack, step_id}`` when the flow
fails.

Early return
------------

A flow can return before its last step. ``early_return`` on the flow is a JS
expression that, when it evaluates true, ends the run early with the result so
far. A per-step ``stop_after_if`` stops the flow after that step.

Workflows as code
=================

Instead of a YAML DAG, a single TypeScript or Python script can orchestrate
sub-jobs through the ``wmill`` SDK. The sub-jobs are tracked in
``workflow_as_code_status`` and keep full observability. Reach for this when
the control flow is easier to express in code than as a graph.

Applying the model here
=======================

A few conventions follow from the model:

- **One step per concern, composed by thin flows.** Promote reusable logic to
  a ``script`` (PathScript) and chain steps with subflows (``flow``) so build,
  boot, and VM steps stay independently testable.
- **N concurrent kernels.** A ``forloopflow`` with ``parallel: true`` and
  ``parallelism: N`` over a list of refs and defconfigs runs one build per
  iteration; the container cgroups keep ``make --jobs=$(nproc)``
  self-balancing.
- **Build then boot.** On one worker group, ``same_worker: true``: step one
  returns the manifest and step two reads ``results.build.bzImage``. Across
  worker groups (for example a bringup that builds on ``default`` and boots on
  a VM group), the manifest points at the published run layer in ``/nix/store``,
  which every worker mounts, so no shared tree is needed.
- **Worktree isolation.** Each worker builds in its own warm ``main`` worktree
  ``workers/<WORKER_INDEX>/main/<project>``, cut from the durable bare repo
  (``$SYSTEM_DIR/bare/<project>.git``) and synced to the requested ref every
  build: parallel across workers, incremental across runs. This is the subject
  of ADR 0001.
- **Dedicated VM workers (future).** Tag VM steps and run a worker group with
  that tag; until then everything runs on the ``default`` group.
- **Build reuse and cross-host fetch.** An identical kernel or QEMU build is
  reused or fetched from the Nix store instead of rebuilt, keyed by a
  reproducible build identity. See :doc:`/concepts/build-store` for the model,
  the ``prebuilt`` (``remote`` and ``remote_index``) knobs, and the
  ``store_index`` catalog step.

Operating loop
--------------

The edit-check-deploy-run loop for a flow, keeping git as the source of truth::

   # edit f/<path>.flow/flow.yaml (step source in value.modules[].value.content)
   nix flake check
   wmill sync push --yes
   wmill flow run f/kernel/build --data '{"config_method":"make"}'
   # if edited in the UI instead: wmill sync pull --yes, then commit

``wmill.yaml`` syncs only ``f/**``, so ``docs/`` stays git-only. Keep secrets
as ``$var:`` or ``$res:`` references, never literals.
