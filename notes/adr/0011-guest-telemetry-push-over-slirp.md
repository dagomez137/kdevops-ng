# Guest telemetry pushes to the host over slirp

The monitoring stack (Grafana, Prometheus, Loki beside the Windmill deploy)
needs metrics and journal from every guest. Guests run on QEMU user-mode
networking (`-nic user`) plus a vsock device for SSH; they have no IP the
host can reach without an explicit `hostfwd` forward, and guests are
short-lived and created in bulk by flows, so any per-guest host-side
bookkeeping is a real cost.

The load-bearing fact is that slirp translates a guest connection to
10.0.2.2 (its `vhost_addr`) into a host connection to 127.0.0.1: libslirp's
`sotranslate_out4` (`subprojects/slirp/src/socket.c`) rewrites the
destination to `loopback_addr` unless `restrict=` is set, and the
qemu-system-units templates never set it. A guest can therefore reach a
collector bound on host loopback with zero configuration, and the collector
never listens beyond loopback.

Each guest runs one Grafana Alloy agent (the telemetry profile in the
vendored NixOS flake) that scrapes its embedded node exporter and reads the
journal, then pushes both: Prometheus remote_write to
`http://10.0.2.2:9090/api/v1/write` and the Loki push API at
`http://10.0.2.2:3100/loki/api/v1/push`. Prometheus runs with
`--web.enable-remote-write-receiver` and an empty scrape list. Per-guest
identity is the `host` label from the guest hostname, which the nix
render step already bakes per VM.

## Status

accepted

## Considered Options

- **Pull: per-guest `hostfwd` metrics ports plus file-based service
  discovery.** The canonical Prometheus direction: forward a per-VM host
  port to the guest's exporter and have the boot flow write a `file_sd`
  target file with the VM's labels. Rejected: it needs a port allocation
  scheme in the VM templates, a registration and deregistration pair in the
  boot and destroy flows, and it still does nothing for logs, which have no
  pull direction; the journal would need a second, push-based path anyway.
- **Push through vsock.** No IP stack involved at all. Rejected for now:
  Alloy speaks TCP, so this needs a socket proxy on both ends for no gain
  over the slirp path that already exists; it remains the fallback if a
  deployment ever runs with `restrict=on`.
- **Push over slirp to host loopback (chosen).** No per-guest state on the
  host, one guest agent for both signals, collectors stay loopback-only,
  and the same agent config works for baremetal by pointing the two URLs at
  the monitoring host instead of 10.0.2.2.

## Consequences

- Liveness inverts: a dead guest goes stale instead of flipping `up` to 0.
  Dashboards encode freshness (`time() - timestamp(<series>)`) as the
  liveness signal.
- The push endpoints are defaults in the flow form fields, not in the
  vendored module: the telemetry profile takes `metrics.url` and `logs.url`
  with no defaults, keeping the vendored flake consumer-agnostic.
- Multi-host fan-in composes naturally: a remote host runs a host-side
  Alloy that receives on the same two loopback ports and forwards to the
  primary, so remote guests keep identical URLs.
- The guest Alloy WAL lives on the tmpfs root, so a crashed guest loses up
  to one flush interval of samples. Accepted for test guests.
