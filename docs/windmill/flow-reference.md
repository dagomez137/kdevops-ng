# Windmill flow & script reference (authoritative)

A working reference for authoring Windmill flows/scripts in this project. Derived
from the real OpenFlow spec and worker source in the windmill repo, **not** the
public docs page (`https://www.windmill.dev/docs/openflow` is simplified and
out of date — it lists 6 module types and `deno`/`python3` only; the real model
below has 9 module types and 23 languages).

Authoritative sources (in the windmill checkout):
- `openflow.openapi.yaml` — the OpenFlow schema (object model below).
- `backend/windmill-worker/src/worker_flow.rs` — flow execution.
- `backend/windmill-worker/src/bash_executor.rs` — bash arg + result handling.
- `backend/parsers/windmill-parser-bash/src/lib.rs` — bash arg inference.

## Object model

```
OpenFlow                       # a .flow/flow.yaml file
├─ summary        string       # plain text (heading; NOT markdown-rendered)
├─ description    string       # GitHub-Flavored Markdown on the flow detail page
├─ schema         JSON Schema  # the flow's inputs (properties/order/required)
└─ value: FlowValue
   ├─ modules: FlowModule[]    # the steps, run in sequence (DAG order)
   ├─ failure_module           # runs on flow failure; id must be "failure"
   ├─ preprocessor_module      # runs before step 1 on external triggers; id "preprocessor"
   ├─ same_worker      bool    # pin all steps to one worker + share ./shared (see below)
   ├─ preserve_step_tags bool  # let steps keep their own tag under a flow tag
   ├─ concurrent_limit / concurrency_key / concurrency_time_window_s
   ├─ debounce_* / max_total_debounc*
   ├─ skip_expr        string  # JS expr to skip the whole flow
   ├─ early_return     string  # JS expr to return early
   ├─ cache_ttl        number  # cache flow results (s)
   ├─ flow_env         object  # env for all steps; values may be "$var:path"/"$res:path"
   └─ priority         number  # higher runs first
```

Each `FlowModule` (step):

```
FlowModule
├─ id            string        # reference its result as results.<id>; used in worktree paths
├─ value: FlowModuleValue      # the 9 types below (discriminated by .type)
├─ summary       string
├─ skip_if       {expr}        # JS -> true skips this step
├─ stop_after_if {expr, skip_if_stopped, error_message}   # stop flow after this step
├─ stop_after_all_iters_if     # loops only: stop after all iterations
├─ sleep         InputTransform# delay before running (s or expr)
├─ cache_ttl     number
├─ timeout       InputTransform# max seconds (static or expr)
├─ mock          {enabled, return_value}   # return mock instead of running (testing)
├─ suspend       {...}         # approval / resume step (see below)
├─ retry: Retry                # constant / exponential / retry_if
├─ continue_on_error bool      # flow continues even if this step fails
└─ priority      number
```

## The 9 FlowModuleValue types (`value.type`)

| `type`         | Schema        | Use |
|----------------|---------------|-----|
| `rawscript`    | RawScript     | inline code (our default — the bash lives here) |
| `script`       | PathScript    | call a saved script by `path` (+ optional `hash`, `tag_override`) |
| `flow`         | PathFlow      | call another flow as a **subflow** by `path` |
| `forloopflow`  | ForloopFlow   | iterate over an array; `parallel` + `parallelism` |
| `whileloopflow`| WhileloopFlow | loop while a condition holds (use `stop_after_if`) |
| `branchone`    | BranchOne     | first matching `expr` branch runs, else `default` |
| `branchall`    | BranchAll     | all branches run (`parallel` opt.); per-branch `skip_failure` |
| `identity`     | Identity      | pass-through (placeholder/debug) |
| `aiagent`      | AiAgent       | tool-calling LLM step (provider, tools, output_schema, …) |

### RawScript (inline) — the one we use
```yaml
value:
  type: rawscript
  language: bash         # deno bun python3 go bash powershell postgresql mysql
                         # bigquery snowflake mssql oracledb graphql nativets php
                         # rust ansible csharp nu java ruby rlang duckdb
  content: |             # the source; for non-bash, export a `main(...)`
    ...
  input_transforms: {}   # maps each arg name -> static/javascript (see below)
  # optional per-step controls:
  tag: <worker-group>            # route this step to a worker group
  concurrent_limit / concurrency_time_window_s / custom_concurrency_key
  lock: <deps lockfile>
  assets: [{path, kind: s3object|resource|ducklake}]
```

### PathScript / PathFlow (composition)
`script` reuses a deployed script (`path`, optional `hash`, `tag_override` to
re-route worker group). `flow` runs a deployed flow as a subflow. Both take
`input_transforms`. This is how kdevops should compose: small reusable scripts
(`f/kernel/*`, `f/vm/*`) chained by thin orchestrator flows.

### Loops & branches
- `forloopflow`: `iterator` is a JS expr returning an array; inside, use
  `flow_input.iter.value` and `flow_input.iter.index`. `parallel: true` +
  `parallelism: N` runs N iterations concurrently. `skip_failures: true` lets
  failed iterations return null instead of aborting.
- `whileloopflow`: repeats `modules` until a `stop_after_if` fires.
- `branchone`: branches evaluated in order, first `expr==true` wins, else
  `default`. `branchall`: every branch runs (set `parallel`), `skip_failure`
  per branch.

## input_transforms — wiring data between steps

Each key in `input_transforms` is an **argument name** of the step's code; its
value is one of:
- `{type: static, value: <any>}` — constant (use `$res:path` for resources).
- `{type: javascript, expr: <JS>}` — evaluated at runtime.

Variables available in `expr`:
- `flow_input.<prop>` — the flow's inputs (from `schema.properties`).
- `results.<step_id>` — a previous step's result.
- `flow_input.iter.value` / `.index` — inside a forloop.
- `error` / `result` — inside `retry_if` / `stop_after_if`.

Example (our build step):
```yaml
input_transforms:
  git_ref:   { type: javascript, expr: flow_input.git_ref }
  defconfig: { type: javascript, expr: flow_input.defconfig }
```

## Bash specifics (what our flows rely on)

**Argument inference** (`windmill-parser-bash`): args come from *contiguous*
top-of-file lines matching `name="$N"` or `name="${N:-default}"`, starting at
`$1`. The parser stops at the first gap, so declare `$1..$N` with no holes.
Later references like `$SANDBOX` or `${WM_ROOT_FLOW_JOB_ID:-…}` do **not** create
phantom args (they aren't `"$<digit>"`). Omitted optional inputs can arrive as
the literal string `null` — normalize them.

**Result capture** (`bash_executor.rs`), in priority order:
1. a `result.json` file written in the cwd (the job dir) → returned as JSON.
2. a `result.out` file → returned as a string.
3. otherwise the **last line** of stdout, trimmed.

→ We write `result.json` to emit a structured manifest (bzImage path,
kernelrelease, commit…) that downstream steps consume.

**Injected env** (a subset): `WM_JOB_ID` (this step's job), `WM_ROOT_FLOW_JOB_ID`
(stable per whole flow run — the custom-worktree job-id fallback), `WM_FLOW_JOB_ID`,
`WM_FLOW_PATH`, `WM_WORKSPACE`, `WM_USERNAME`, `WM_PERMISSIONED_AS`. WHITELISTed
host env (`WORKER_INDEX`, `WORKERS_DIR`, the D-Bus socket vars) is injected by our
worker quadlet.

## Passing files between steps: `same_worker` + `./shared`

Step results are JSON. To hand **files** from one step to the next, set
`value.same_worker: true` on the flow and use the shared directory: the worker
keeps the job dir and bind-mounts `<job_dir>/shared` at `/tmp/shared`
(`./shared` from the step). (`worker.rs:4795`.)

**kdevops implication:** our per-worker sandbox `$WORKERS_DIR/<index>` is mounted
only into that worker's container. So a build on worker `0000` writes artifacts
only `0000` can see. A multi-step pipeline that builds a kernel then boots it in
QEMU **must** either:
- set `same_worker: true` so build+boot land on the same worker (then the
  manifest's `bzImage` path is reachable, or use `./shared`), or
- write artifacts to `$WORKERS_DIR/shared/...` (mounted rw in every worker) and
  pass the path via the result manifest.

`same_worker` is the clean default for build→boot. For dedicated VM-lifecycle
workers later, give those steps a `tag` and run a worker group with that tag.

## Approval / suspend steps

`module.suspend`: `required_events` (approvals needed), `timeout`,
`resume_form.schema` (collect input on resume), `user_auth_required`,
`user_groups_required`, `self_approval_disabled`, `continue_on_disapprove_timeout`.
Resume/cancel via `/w/<ws>/jobs/resume/<job_id>` and `/jobs/cancel/<job_id>`.

## Retries & error handling

- `retry.constant {attempts, seconds}` or
  `retry.exponential {attempts, multiplier, seconds, random_factor}`.
- `retry.retry_if.expr` — JS over `result`/`error` to decide whether to retry.
- `continue_on_error` on a step; `failure_module` (id `failure`) on the flow gets
  `{message, name, stack, step_id}`.

## Workflows as code

Instead of a YAML DAG, a single TS/Python script can orchestrate sub-jobs via the
`wmill` SDK; sub-jobs are tracked in `workflow_as_code_status` and show full
observability. Use when control flow is easier expressed in code than as a graph.

## Operating loop (code-as-git)

```
# edit f/<path>.flow/flow.yaml  (script in value.modules[].value.content)
make style
wmill sync push --yes
wmill flow run f/kernel/build --data '{"config_method":"make","defconfig":["tinyconfig"]}'
# if edited in the UI instead: wmill sync pull --yes, then commit
```
`wmill.yaml` syncs only `f/**`, so `docs/` stays git-only. Keep secrets as
`$var:`/`$res:` refs, never literals.

## kdevops application notes

- **One step per concern, composed by thin flows.** Promote reusable logic to
  `script` (PathScript) and chain with subflows (`flow`) so build/boot/VM steps
  are independently testable.
- **N concurrent kernels:** a `forloopflow` with `parallel: true,
  parallelism: N` over a list of refs/defconfigs — each iteration is one build;
  the container cgroups keep `make --jobs=$(nproc)` self-balancing.
- **build → boot:** same worker group, `same_worker: true`; step 1 returns the
  manifest, step 2 reads `results.build.bzImage`. Across worker groups (e.g. bringup
  builds on `default`, boots on `vm`) the manifest points at the published run layer
  in `/nix/store`, which every worker mounts — no shared tree needed.
- **worktree isolation:** each worker builds in its own warm `main` worktree
  `workers/<WORKER_INDEX>/<namespace>/main/<canonical>`, cut from the durable Bare
  (`$SYSTEM_DIR/bare/<namespace>/<canonical>.git`) and synced to the requested ref
  every build — parallel across workers, incremental across runs. See
  [`CONTEXT.md`](../../CONTEXT.md) and
  [ADR-0001](../adr/0001-bare-is-the-working-repo.md).
- **dedicated VM workers (future):** tag VM steps and run a worker group with
  that tag; until then everything runs on the `default` group.
- **build reuse & cross-host fetch (the Store):** an identical kernel/QEMU build is
  reused or fetched from the Nix store instead of rebuilt, keyed by a reproducible
  build identity. See [the build Store](store.md) for the model, the `prebuilt`
  (`remote`/`remote_index`) knobs, and the `store_index` catalog step.
