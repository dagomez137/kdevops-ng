# Live job output: a tee from the worker to the client

Status, 2026-10-09: sections 1 to 6 implemented, with the changes under "As
built", and deployed on A. Fork
commits on v1.824.1: `06e482dea8` worker and api tee, `f2635c0165` cli live
stream, `8404d5e32c` flow_changed and progress events, `b42076be17` cli
fallback debug line, `d81dca348d` worker and host in start events,
`4350d4dfbb` cli progress view (`flow run`; `--follow`, `--no-view`),
`07cf9d3ec2` every step, with its job id, listed at the end. The view
is one fixed region redrawn in place: a bar with a slot per flow step
(subflows flattened), the current step with its worker, host and `wmill job
logs` command, and an output window (`w`, `+`/`-`). It writes no log copy;
logs stay in Windmill. Measured: a 0.5 s ticking probe reached a subscriber
within 1 ms with offsets matching `get_logs`; the region held 6 rows over all
130 redraws of a bringup. Not done: multi-server routing (section 3), the
workspace-wide view, B's workers (API port forward plus `LIVE_LOGS_URL`), and
the upstream pull request. User docs: `docs/getting-started/wmill.rst`.

## As built

Where the build departs from the sections below:

- Transport (section 2): one streamed NDJSON POST per job to
  `{LIVE_LOGS_URL}/api/w/{w}/jobs_u/live/{job}/publish`, authenticated with
  the job's own token, not one WebSocket per worker at `BASE_INTERNAL_URL`.
  A per-job stream needs no multiplexing and no new worker credential.
- Hub (section 3): a `Mutex<HashMap>` with a 1 MiB backlog per job and per
  root flow, kept 120 s after `End`; no cross-server routing yet.
- Client (section 4): no `from` parameter. The client subscribes, then
  backfills from `get_logs`; `no_backlog` skips the replay.
- Step events (section 5): the server emits `Start` (with worker and host)
  when a job's publish stream opens and `End` when it closes; flow progress
  is a `flow_changed` notice after each flow-status update, from which the
  client reads the flow's state. `set_progress` arrives as `Progress`.
- View (section 6): one bar with a slot per step (not counters), an output
  window instead of a footer of live steps, and the steps listed with their
  job ids at the end. No workspace-wide view.

## Problem

A line a step prints reaches `wmill flow run` 0.5-8 s later, in bursts.
Measured on `f/qsu/bringup` (reuse run, 86 s): 1273 lines arrived at 150
distinct instants; every long gap is a multiple of 3.0 s.

| Stage | Delay | Where (v1.824.1) |
|---|---|---|
| Worker batches lines before one DB append | 0.5 s (<10 s), 2.5 s (<60 s), 5 s | `windmill-worker/src/handle_child.rs:423-438` |
| SSE handler re-reads the job row | 3000 ms unless `fast` (then 100/500, decays to 3000 at i>100) | `windmill-api/src/jobs.rs:10816` |
| Fork CLI subscribes to SSE without `fast` | adds the 3 s tick | fork `19013bc505`, `cli/src/commands/script/script.ts` |
| CLI sleeps after each step | 1000 ms | `script.ts` (`track_job`) |

Upstream's CLI busy-polls `getJobUpdates` with no sleep (no tick, but a
request storm). No push path exists: upstream replaced LISTEN/NOTIFY with
a polled `notify_event` table (e860847073, 30 s default) for config events.

Tuning the polls (`fast`, shorter batches) only shrinks the latency and
multiplies DB load. The target is zero added latency: a line is on the
client as soon as the worker reads it, whatever host the worker is on.

## Shape

```
                       ┌──────────────── unchanged ────────────────┐
child ─pipe─▶ worker: read line ─▶ mask ─┬─▶ batch ─▶ Postgres (truth)
                                         │
                                         └─▶ live frame ─ws─▶ API server ─▶ hub ─SSE─▶ CLI / UI
```

The database path stays the source of truth (replay, history, gap fill).
The live path is best-effort and lossy by design: it never blocks the
child, and every loss is detectable and repaired from the database.

## 1. Worker: one sink per job

Tap the read loop in `handle_child.rs` right after masking (`logged`,
before `append_with_limit`), so the live copy has secrets masked and the
same size limit as the stored log. Every other writer of a job's log
(the `job=... worker=...` header, dependency install output, executor
banners) goes through `append_logs`; route those through the same sink so
the live stream and the stored log are the same byte sequence.

Frames carry the DB coordinate, not a private sequence number:

```
Frame { job, root, step: Option<flow_step_id>, offset, text }   // text = masked lines
Frame { job, kind: Gap { from, to } }                           // live copy dropped
Frame { job, kind: End { offset, status } }                     // job finished
```

`offset` counts characters the way Postgres `CHAR_LENGTH` does (code
points), seeded once at job start from `log_offset + CHAR_LENGTH(logs)`.
A JS client must count code points too, not UTF-16 units.

The sink is a bounded channel drained by a background task. When it is
full the worker drops frames and later emits one `Gap`; it never waits.
Frames are coalesced only while the channel is busy, so an idle link
forwards each line immediately.

## 2. Transport: worker to API server

A worker on another host (B: `hetzie`) reaches A's Postgres over an SSH
tunnel but not necessarily A's API. Three candidates:

- **Postgres NOTIFY** reuses the connection every worker already has, but
  each notifying commit takes a cluster-wide lock, payloads cap at 8000
  bytes, the queue is shared with the job queue's database, and it breaks
  under a transaction-mode pooler. Per-line NOTIFY from 20 workers puts
  log volume on the queue's critical path. Rejected.
- **Local file + tail**: same-host only. Rejected.
- **WebSocket from worker to API server**: one connection per worker
  (not per job), multiplexing frames of all its jobs, authenticated with
  the worker's existing token. Upstream already supports workers that
  talk HTTP only (agent workers, `Connection::Http`), so "worker can reach
  the API" is a supported topology. Chosen.

The worker dials `BASE_INTERNAL_URL` (`/api/workers/live`). If the
connection is down, frames are dropped (with `Gap`), and it reconnects
with backoff; the DB path is unaffected.

Deployment consequence: B workers need A's API reachable, i.e. the B→A
tunnel forwards the API port as well as 5432.

## 3. Server: in-memory hub, routed across servers

`LiveHub: DashMap<JobId, Channel>` where `Channel` is a
`tokio::sync::broadcast` plus a ring of the last ~64 KiB of frames, so a
subscriber that arrives a moment late gets the recent backlog from memory.
Frames are also published on the root job's channel, so one subscription
to a flow sees every leaf step.

One server is all we run. For several API servers, the server that owns a
worker's socket records `live_route(job, server_addr)` once per job (one
row, not per line). A subscriber that lands elsewhere is proxied
server-to-server to the owner. Per-line traffic never touches the
database.

Lifecycle: channel created on the first frame, closed on `End`, ring
dropped after a short grace period.

## 4. Client protocol: subscribe, then backfill

`GET /jobs_u/live/{id}?from=<offset>` (SSE), same access check as
`getupdate`:

1. Subscribe first and buffer frames.
2. Read the stored log from `from` to the DB's current end (`get_logs`,
   the only complete copy; the hot window alone is not enough).
3. Print it, then flush buffered frames whose `offset` is past what was
   printed, dropping overlap.
4. On `Gap { from, to }` or a reconnect, backfill that range from the DB.
5. On `End`, verify the final offset against the stored log; backfill any
   tail.

This keeps fork commit `19013bc505`'s contiguous-coverage logic. It moves
from being the streaming path to being the backfill path. Without `fast`
and without the 3 s poll, the SSE `getupdate` remains only as a fallback
for a server without the hub.

## 5. Step events (the TUI's input)

The worker that runs a flow's state machine (`worker_flow.rs`) publishes
step transitions on the root channel: `StepQueued`, `StepStarted { job,
step_path, worker }`, `StepDone { status, skipped, duration }`, plus
`Progress { job, pct }` from `wmill.set_progress`. One subscription then
gives the client BitBake's event stream: the CLI is a reducer over
events, with no per-step polling and no 100 ms `getJob` loop.

## 6. CLI view (knotty model)

- Footer of live steps, redrawn in place (`cuu` + `ed`), dirty-flag, ≤40 Hz,
  forced refresh every 5 s for timers; log lines print above it.
- Counters: `Steps: x of y (r reused, s skipped)`; `reuse_check` hits are
  the sstate analogue.
- Output of steps hidden by default; a key toggles streaming the focused
  step above the footer; `--follow` does it without a TTY.
- Failure: the last N lines of the failed step plus `wmill job logs <id>`.
- Non-TTY: one line per step transition, no footer.
- No local log copy (CIJOE's per-run directory was tried and dropped): the
  view names each step's job id for `wmill job logs`.
- A workspace-wide view lists every running flow, one row each, since
  parallelism in Windmill comes from many flows across workers.

## Open questions

- Frontend: `JobLoader.svelte` could use the same endpoint; not in scope.
- Ring size and coalescing thresholds need a measurement on a `make --jobs`
  build log.
