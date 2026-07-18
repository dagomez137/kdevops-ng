<\!--
Status: EXECUTED and live-validated (all six phases), 2026-07-06..07.
This is the approved plan, offloaded here as a record. Durable summary:
memory grafana-monitoring-stack; session handoff:
notes/handoff/handoff-grafana-monitoring.md; ADR:
notes/adr/0011-guest-telemetry-push-over-slirp.md.
Note: earlier in the same plan-mode file this held the firmware
kselftest plan, overwritten when monitoring planning began; that work
is recorded in notes/handoff/handoff-firmware-per-scenario-units.md and
its commits, not here.
-->

# First-class Grafana monitoring for kdevops-ng guests and hosts

## Context

Make the NixOS closure (QEMU guests, later baremetal) a first-class
monitored system: each guest continuously exposes CPU, RAM, disk/net,
and its journal to an OPTIONAL Grafana server deployed with nix beside
Windmill, its workers, and the existing PostgreSQL. Then correlate test
suite runs with metrics (Grafana annotations over the exact run
window), keep it expandable (buddy allocator / zone / NUMA stats are a
collector flag away), and make it eBPF-ready (blkalgn from the
`dagmcr/bcc` fork; continuous histograms via a packaged exporter
later). Upstream sources are cloned for reference at
`~/src/grafana-labs/{grafana,loki,alloy}` (docs under `docs/sources/`).

## Verified facts (do not re-derive)

- **Push transport works**: slirp translates guest connections to
  10.0.2.2 into host 127.0.0.1
  (`~/src/qemu-project/qemu/subprojects/slirp/src/socket.c:954`
  `sotranslate_out4`; `vhost_addr` in `net/slirp.c:447`; qsu never sets
  `restrict=`). N ephemeral guests push to loopback-bound collectors
  with zero host-side registration.
- **Per-VM identity already exists**: `f/nix/render_config.py:296`
  bakes `networking.hostName = <vm_name>` into each per-VM config;
  Alloy's `constants.hostname` labels every series/log line.
- **Alloy has every needed component** (verified in
  `~/src/grafana-labs/alloy/docs/sources/reference/components/`):
  `prometheus.exporter.unix` (embedded node_exporter),
  `prometheus.scrape`, `prometheus.remote_write`,
  `prometheus.receive_http`, `loki.source.journal`, `loki.source.api`,
  `loki.write`; stdlib `constants.hostname`.
- **Buddy allocator stats are built in**: `prometheus.exporter.unix`
  collectors `buddyinfo`, `zoneinfo`, `meminfo_numa` exist, disabled by
  default, enabled via `enable_collectors`.
- **Grafana annotations**: `POST /api/annotations`
  `{time, timeEnd (ms epoch), tags: [str], text}`
  (`grafana/pkg/api/dtos/annotations.go:9`). Config-by-env is
  `GF_<SECTION>_<KEY>`; Grafana supports postgres as its database.
- **nixpkgs packages**: grafana 13.0.3, grafana-alloy 1.16.0,
  prometheus 3.12.0, grafana-loki 3.7.3, prometheus-node-exporter,
  bcc, bpftrace. Cloudflare `ebpf_exporter` is NOT packaged.
- **Existing guest monitoring**:
  `vendor/nixos-flake/modules/profiles/monitoring.nix` already ships
  run-scoped `monitor-{sysstat,cpu-governor,blkalgn,biolatency}@<run-id>`
  units (file outputs to a virtiofs-backed dir); `pkgs/libbpf-tools.nix`
  supports a src override to the `dagmcr/bcc` `blkalgn-libbpf` branch,
  already curated via `render_config.py` `_OVERRIDABLE_PKGS`. The new
  work COMPOSES with this (different lifecycle), never replaces it.
- **Deploy pattern**: one flake package + one `systemd --user` unit +
  optional `%E/...env` + out-link under `%S/.../pkgs/`; PostgreSQL 16
  user unit on 127.0.0.1:5432 + `%t/windmill` socket; everything
  loopback + SSH forward.
- Journal records carry `__REALTIME_TIMESTAMP`; wait steps currently
  read only `__MONOTONIC_TIMESTAMP` (`f/selftests/wait.py:58`).

## Design decisions

- **Push, not pull**: guest Alloy `prometheus.remote_write` to
  `http://10.0.2.2:9090/api/v1/write` and `loki.write` to
  `http://10.0.2.2:3100/loki/api/v1/push`. Prometheus runs with
  `--web.enable-remote-write-receiver`. No hostfwd ports, no file_sd,
  no boot-flow registration; logs are push-native; multi-host later is
  aggregate-and-forward. Accepted cost: liveness is staleness-based,
  encoded in dashboards.
- **Single guest agent: Grafana Alloy** (embedded node_exporter +
  journal shipping + future ebpf-exporter scrape, one config, one
  unit). Store size is a host cost only (virtiofs `/nix/store`).
- **New `modules/profiles/telemetry.nix`** in vendor/nixos-flake
  (continuous boot-to-shutdown export), distinct from the run-scoped
  `monitoring.nix`. Unit name `alloy.service` (canonical upstream
  name). Options `nixos-flake.telemetry.*`. Consumer-agnostic: NO
  10.0.2.2 defaults in the module (subrepo rule); defaults live in the
  render_config form fields.
- **Server stack in deploy/nix**, `monitoring-*` namespace: three
  packages + three units (`monitoring-grafana`, `monitoring-prometheus`,
  `monitoring-loki`), own loopback ports (3000/9090/3100), SSH-forward
  access like Windmill (no Caddyfile change; a `/grafana/*` route +
  `GF_SERVER_ROOT_URL` is a documented follow-up). Grafana state in a
  `grafana` database in the SHARED postgres via a new
  `monitoring-db-setup` (own script; windmill's db-setup stays
  ignorant of the optional stack). Datasources + dashboards are
  provisioning-as-code under `deploy/nix/monitoring/` (git as truth).
- **Annotations as a step**: new `f/monitoring/annotate.py` appended
  after report/judge in suite flows; stdlib `urllib.request` (the
  no-bare-subprocess rule governs process execution; the step prints
  method, URL, and JSON body before sending). Endpoint+token in a
  Windmill resource `f/monitoring/grafana` (`base_url`, `token`);
  graceful logged no-op when absent so suites run without the stack.
- **eBPF phased**: run-scoped `monitor-blkalgn@`/`monitor-biolatency@`
  stay the per-run capture now (annotations make their windows
  findable); continuous histograms land later via a new
  `vendor/nixos-flake/pkgs/ebpf_exporter.nix` scraped by guest Alloy.

## Suite-to-metrics mapping (drives dashboards and defaults)

| Suite | Metrics of interest | Source |
|---|---|---|
| fstests, blktests | disk I/O rate/latency, PSI io, dirty pages | node collectors now; biolatency/blkalgn histograms (run-scoped now, ebpf_exporter later) |
| mm-flavored (usertests vma, mmtests) | buddyinfo, zoneinfo, meminfo_numa, vmstat | `extraCollectors` (Phase 4) |
| selftests, kunit, runtime-tests | CPU, RAM, journal (unit + kmsg), module load churn | base Alloy config |
| all | run window + verdict annotation; journal in Loki | Phase 3 |

## Phase 1: optional monitoring server stack (deploy/nix)

Add:
- `deploy/nix/monitoring/prometheus.yml` (empty scrape_configs; push
  model), `loki.yaml` (single-binary, filesystem tsdb under
  `%S/monitoring/loki`, 127.0.0.1:3100),
  `grafana/provisioning/datasources/datasources.yaml` (Prometheus
  127.0.0.1:9090 + Loki), `grafana/provisioning/dashboards/` +
  `dashboards/guest-overview.json` (CPU/RAM/load/disk/net + logs panel
  per `host`, staleness-based liveness).
- `deploy/nix/bin/monitoring-db-setup`: over the `%t/windmill` socket
  create role+db `grafana` (password under `%S/monitoring/secrets/`),
  write `%S/monitoring/env/grafana-database.env` (`GF_DATABASE_*`,
  `GF_SERVER_HTTP_ADDR=127.0.0.1`).
- `deploy/nix/systemd/monitoring-{grafana,prometheus,loki}.service`
  following the windmill unit pattern (grafana Requires/After
  windmill-db.service; prometheus gets
  `--web.enable-remote-write-receiver`; Loki's single-dash flags are a
  documented long-form exception).

Modify:
- `deploy/nix/flake.nix`: packages `grafana`, `prometheus`, `loki`
  (from nixpkgs), `monitoring-db-setup`.
- `nix/apps/default.nix`: `monitoring-build/install/activate/
  deactivate/deploy` apps mirroring the windmill ones.
- `docs/deployment/monitoring.rst` (new, in toctree); `cmd_links`
  entries for `grafana`, `prometheus`, `loki`, `alloy`.
- `notes/adr/0011-guest-telemetry-push-over-slirp.md`: push decision +
  slirp evidence + rejected pull alternative.

Commits: packages+db-setup; units+configs+provisioning; apps; docs; ADR.

Done when: `nix run .#monitoring-deploy` brings all three units
active; `/-/ready` endpoints answer; Grafana over SSH forward shows
both datasources healthy and grafana tables exist in the `grafana`
DB; stopping `monitoring-*` leaves windmill untouched.

## Phase 2: guest telemetry profile + flow wiring (the basics, e2e)

vendor/nixos-flake (own commit rules: <=50-char subject, verify
options, `nix fmt` + `nix flake check`, never name downstream):
- `modules/profiles/telemetry.nix`: options `enable`, `metrics.url`,
  `logs.url` (no defaults), `scrapeInterval` (15s), `extraCollectors`
  (default `[]`; description names buddyinfo/zoneinfo/meminfo_numa).
  Config: `environment.etc."alloy/config.alloy".text` wiring
  exporter.unix -> scrape -> remote_write (external label
  `host = constants.hostname`) and journal -> loki.write (`unit`
  label); `systemd.services.alloy` (StateDirectory=alloy,
  Restart=on-failure, After/Wants network-online.target). Note the
  tmpfs WAL-loss caveat in the module comment.
- `flake.nix`: register `profiles.telemetry` + an
  `imageless-telemetry` check.

Main repo:
- `f/nix/render_config.py`: add `telemetry` to `_PROFILES`/
  `_PROFILE_ENABLE`; two curated inputs `telemetry_metrics_url` /
  `telemetry_logs_url` defaulting to the 10.0.2.2 URLs (schema titles
  spell out Prometheus remote_write / Loki push). Not featured by
  default (server is optional).
- Regenerate `f/qsu/bringup.flow` (`python3 scripts/gen-bringup.py`);
  re-pin consumer locks (`update_lock` already defaults true).
- Docs: telemetry section in `docs/flows/guests.rst` linking the
  deployment page.

Done when: two VMs brought up with telemetry selected show
`node_cpu_seconds_total{host="<vm>"}` for both and Loki `{host=...}`
journal with `unit` labels; a VM without telemetry boots unchanged;
all gates pass (`nix flake check` both repos, style, generated).

## Phase 3: run annotations (match suite execution to metrics)

- Wait steps (`f/selftests/wait.py`, `f/fstests/wait.py`, then the
  sibling suites): also capture `__REALTIME_TIMESTAMP` at
  MSG_UNIT_STARTING/terminal records; return `started_realtime_ms` /
  `ended_realtime_ms`.
- New `f/monitoring/annotate.py`: resource-driven
  (`f/monitoring/grafana` resource type: `base_url`, `token`; token is
  a one-time operator-minted Grafana service-account token, documented,
  never auto-minted), posts the region annotation with tags
  `[suite, vm, kernel, verdict]` (+ collection/section where present);
  prints the request before sending; logged no-op without the resource.
- Append the annotate step to `f/selftests/run.flow`,
  `f/fstests/check.flow`, then the remaining suite flows.
- Docs: one shared subsection in `docs/flows/guests.rst`.

Done when: a selftests run produces an annotation spanning the exact
window with the guest's CPU/journal visible under it; run without the
resource still green with a "skipping" log line.

## Phase 4: expandable node metrics (buddy allocator etc.)

- Surface `extraCollectors` through `render_config.py` as a curated
  checklist (`buddyinfo`, `zoneinfo`, `meminfo_numa`, `processes`...
  with human labels, default empty); regenerate bringup.
- `deploy/nix/monitoring/dashboards/mm.json` (per-order buddy blocks,
  per-zone free pages, NUMA meminfo) keyed on `host`.

Done when: enabling `buddyinfo` on one VM yields
`node_buddyinfo_blocks{host=...}` and the other VM shows none.

## Phase 5: eBPF histograms

- Document now: run-scoped monitor-blkalgn@/biolatency@ (fork override
  `dagmcr/bcc` branch `blkalgn-libbpf` via the existing
  `_OVERRIDABLE_PKGS` libbpf-tools override) remain the per-run
  capture, discoverable via Phase 3 annotations.
- Then: `vendor/nixos-flake/pkgs/ebpf_exporter.nix` (buildGoModule of
  cloudflare/ebpf_exporter + clang-compiled BPF objects; guest kernel
  BTF already enabled via ebpf.config); `telemetry.ebpf.enable` +
  `telemetry.ebpf.configs` running it on 127.0.0.1:9435, scraped by
  guest Alloy into the same remote_write. Highest-uncertainty item,
  deliberately last and droppable.

Done when: `nix build path:vendor/nixos-flake#ebpf_exporter` succeeds
and a guest with it enabled streams bio-latency buckets to Grafana
during an fstests run, with the run-scoped monitors unchanged.

## Phase 6: multi-host fan-in and baremetal

- Baremetal: same telemetry profile, `metrics.url`/`logs.url` pointed
  at the monitoring host (the 10.0.2.2 defaults are form-field
  defaults only).
- Remote hosts (hetzie-class): `monitoring-collector.service` running
  host-side Alloy (`prometheus.receive_http` :9090 +
  `loki.source.api` :3100 on loopback, forwarding to the primary), so
  remote guests keep identical URLs; transport to the primary is a
  documented persistent SSH tunnel unit matching the peer-SSH posture.

Done when: a guest on host B appears in host A's Grafana with its
`host` label intact.

## Risks

- Prometheus `--web.enable-remote-write-receiver` on 3.12 verified at
  Phase 1 deploy (`prometheus --help`); fallback: host-side Alloy
  `prometheus.receive_http` in front.
- Staleness-based liveness must be encoded in dashboards or operators
  misread dead guests.
- Alloy WAL on tmpfs root loses up to one flush interval on crash
  (accepted for test guests, noted in module).
- Grafana service-account token is a one-time manual mint (documented
  operator action).
- `ebpf_exporter` nix packaging (BPF objects in the sandbox) is the
  main unknown; isolated to Phase 5.

## Execution notes

- python-expert for the step/flow code (annotate.py, wait extensions,
  render_config); I do nix modules, units, configs, docs directly.
- Per-subproject commit rules (vendor/nixos-flake 50-char subjects;
  main repo 75-char + trailers); scoped staging; gates per phase:
  `nix flake check`, `scripts/check-style.sh`, vendored `nix fmt` +
  `nix flake check`, Sphinx `-W`, `gen-bringup.py --check`.
- Deploy per phase: `nix run .#monitoring-*` apps (Phase 1), vendored
  flake bump + `wmill sync push` + bringup rebuild (Phase 2+).
