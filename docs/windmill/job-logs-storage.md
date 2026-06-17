# Job log storage (and the shared logs volume)

How Windmill persists job logs, why a split server/worker deployment needs a
shared volume, and what is community vs. enterprise. Verified against
`windmill:main` on 2026-06-08.

## How logs are stored

While a job runs, its logs stream into Postgres. Once the accumulated logs cross
`LARGE_LOG_THRESHOLD_SIZE` (a hardcoded `9000` bytes in
`backend/windmill-types/src/jobs.rs`, not configurable), Windmill **offloads the
overflow to a file** instead of growing the DB row, and the DB keeps a marker:

    [windmill] Previous logs have been saved to disk at logs/<job-id>/<ts>_<len>.txt

The file lands under `WINDMILL_DIR/logs` (`WINDMILL_DIR` defaults to
`/tmp/windmill`, so `/tmp/windmill/logs/<job-id>/<ts>_<len>.txt`) on **the node
that ran the job**. Downloading it hits
`GET /api/w/<ws>/jobs_u/get_log_file/<job-id>/<file>.txt`, which reads that local
file (`get_log_file` in `backend/windmill-api/src/jobs.rs`).

## Community vs. enterprise

- **Writing the file to disk works in `windmill:main`** — the published image
  carries the `private` build, whose `default_disk_log_storage` actually writes
  the file. (The pure-OSS `not(feature = "private")` stub is a no-op, but that is
  not what the image ships.)
- **Distributed S3 log storage is enterprise only** — gated behind
  `cfg(all(feature = "enterprise", feature = "parquet"))`. Without it there is no
  object store to fall back to, so the file must be present on the local disk the
  API server reads.

## Why the shared volume

The server and the workers run in **separate containers**. A worker writes the
log file to *its* `/tmp/windmill/logs`; the server looks in *its own* and 404s:

    Not found: File not found on server logs volume /tmp/windmill/logs and no
    distributed logs s3 storage for <job-id>/<ts>_<len>.txt

The fix is the same one the upstream `docker-compose.yml` uses (its `worker_logs`
named volume): mount one shared `/tmp/windmill/logs` across the server, the
native worker and every general worker. The podman deploy does this with a host
bind-mount `%C/windmill/logs:/tmp/windmill/logs` (also handy — the logs are then
inspectable directly on the host under `~/.cache/windmill/logs`).
