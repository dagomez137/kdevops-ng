# Ref-picker dynselect hangs: findings and the preview lane (handoff)

Findings and solutions for the run-form dynselect hangs (2026-08-04 session):
the kernel build form's Ref knob (`dynselect-list_kernel_refs`) sometimes sat
on "Loading..." forever, or rendered a populated list that could not be
opened or selected. A follow-up to `dynselect-debugging.md`, which covered the
nested-dynselect rendering bugs; this one is about job routing and robustness.
Written as a handoff so a fresh session can finish the remaining deploy steps
without re-deriving anything.

## The knob under review

The Ref field in `f/kernel/build.flow/flow.yaml` (also bisect good/bad refs
and the bringup ref pickers) is a `dynselect-list_kernel_refs` whose helper,
embedded in the flow schema's `x-windmill-dyn-select-code`, imports
`f.common.gitrefs.list_refs`. Its git history is three commits: `0cff088`
added the ref lister (packed-refs plus loose refs, no `git` subprocess),
`6d49c2b` wired the pickers into the build forms, and `c3c1e7b` added the
kernel.org `releases.json` merge, which put a network fetch into the
keystroke path.

## Findings (three layers, one symptom)

1. Queue starvation, the dominant hang. A dynselect helper runs as a real
   Windmill job. `run_dynamic_select` pushes it as a Preview-kind raw-code
   job with no tag, so it falls to the language default tag `python3`,
   served only by the two default-group workers, the same pool that runs
   kernel builds. Two builds in flight meant every picker job queued for the
   duration of a build. The repo already applies the cure elsewhere: the
   vm/vm-run tag split exists so a long job never starves a quick one; the
   build pool never got the same split.
2. The "populated but unselectable" presentation. `Select.svelte` disables
   the whole control while `loading && !value`, and `DynamicInput.svelte`
   reports loading whenever a refresh is in flight with the dropdown closed.
   A starved refresh therefore leaves a permanently disabled field, worst on
   pickers without a default (bisect). There was also no timeout: a job
   nobody picked up showed "Loading..." until the page was abandoned.
3. `_korg_releases()` stalls in `f/common/gitrefs.py`. `urlopen(timeout=3)`
   does not bound DNS resolution (`getaddrinfo` has no timeout), so a broken
   resolver blocked each helper job 30s or more; a failed fetch recorded
   nothing, so every keystroke re-paid the attempt exactly when the network
   was worst; the cache write was not atomic, so a concurrent reader could
   see a torn file and silently drop the kernel.org section. The frontend
   additionally submitted (and cancelled) one job per keystroke.

## Solutions

### Applied live on 2026-08-04 (no rebuild was needed)

The deployed fork build (1.741.0, rev `6c36e7e7c1`) already carries
upstream's `preview_tags_override` (#8649): when enabled, Preview-kind jobs
are retagged from their language tag to `preview`. Applied:

- Global instance setting `preview_tags_override = true` (Workers page,
  Manage tags; set via the API in this session).
- Two preview-lane workers: `windmill-worker@0005` and `@0006`, enabled with
  drop-ins at `~/.config/systemd/user/windmill-worker@000{5,6}.service.d/group.conf`
  carrying `WORKER_GROUP=preview` and `WORKER_TAGS=preview`, sandbox dirs
  `workers/0005` and `0006` created under the live workbench.

Verified end to end: a `jobs/run/dynamic_select` call for the deployed
`f/kernel/build` flow (`entrypoint_function=list_kernel_refs`, and note the
frontend also passes `_ENTRYPOINT_OVERRIDE` in `args`; without it the
executor looks for `main` and fails) completed with `tag: preview` on a
`wk-preview-*` worker in 78ms with the expected options. Pickers now answer
instantly while both builders are busy. Known trade-off: editor Test runs
are Preview jobs too and share the lane; that is why there are two
instances. Ordering constraint for reproducing elsewhere: start at least one
`preview`-tagged worker before flipping the setting, or every preview job
queues with no consumer. Revert: set the setting to false and
`systemctl --user disable --now windmill-worker@0005 windmill-worker@0006`.

The unit header in `deploy/nix/systemd/windmill-worker@.service` now
documents the lane (uncommitted).

### In the kdevops-ng working tree, uncommitted

`f/common/gitrefs.py` hardening plus new `tests/test_common_gitrefs.py`
(16 tests; full suite 529 pass; `ruff check` and `ruff format --check`
clean; smoke-tested against the live Bare: 200 options in about 10ms,
output identical to the deployed version). The changes: the fetch runs in a
one-shot thread joined at a 3s wall-clock deadline so DNS cannot stall it; a
failed attempt stamps a 60s sidecar throttle marker
(`cache/korg-releases.json.attempt`) while the stale cache keeps serving;
cache publishes via same-directory temp file and `os.replace`; ref reads
degrade on `OSError` instead of raising; an unparseable payload is never
cached. See the working-tree diff for detail.

Takes effect only after a workspace deploy: the helper imports
`f/common/gitrefs` from the instance database, not from git.

### In the Windmill fork, uncommitted on branch `fix/dynselect-robustness`

`~/src/windmill-labs/windmill`, branch cut from the deployed rev, one file:
`frontend/src/lib/components/DynamicInput.svelte`. A 30s watchdog cancels a
no-show job and surfaces a retryable error line instead of eternal
"Loading..."; filter keystrokes are debounced 250ms before submitting a
helper job (SelectDropdown still narrows the loaded list instantly
client-side); the loading/disabled presentation applies only while no items
are loaded, so a stale list stays selectable during a refresh.
`npx svelte-check` reports no errors in this component (the repo has
pre-existing unrelated codegen drift). The svelte MCP autofixer reported no
issues.

## Remaining work for the next session

1. Deploy the committed gitrefs hardening to the instance, `deploy-staging`
   first: the helper imports `f/common/gitrefs` from the workspace database,
   so the git commit alone changes nothing live.
2. Push the fork's `fix/dynselect-robustness` commit, bump `rev`/`hash` in
   `deploy/nix/windmill/package.nix`, rebuild and roll the instance. Not
   urgent: the preview lane removes the common trigger; the patch is
   defense in depth.
3. Optionally fold the preview-lane bring-up (drop-ins plus setting) into
   `BOOTSTRAP.md` or the deploy docs so a fresh install gets it; today it
   lives only as live systemd state plus the unit-header comment.
4. If a hang recurs despite the lane: check `v2_job_queue` for `preview`-tag
   backlog (a wedged editor Test run can occupy an instance), and check the
   worker journals.

Access notes: API base `http://127.0.0.1:8002` (Caddy TLS wraps it on 8000);
the CLI token lives in `~/.config/windmill/remotes.ndjson` (not reproduced
here).

## Suggested skills

- `jylhis-skills-core:commit-stories` when curating the kdevops-ng and fork
  commits (subjects, splitting, DCO trailers).
- `svelte:svelte-code-writer` (ideally inside the `svelte-file-editor`
  agent) for any further `DynamicInput.svelte` work before the fork push.
- `jylhis-skills-core:diagnose` if the hang reproduces after the lane, to
  keep the reproduce/minimise/instrument discipline instead of re-guessing.
