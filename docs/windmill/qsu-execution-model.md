# qsu / host-systemd execution model (verified)

How a Windmill worker container drives the host to manage QEMU VMs (qsu), what is
reachable, and the constraints that fall out of it. Verified live on 2026-06-08
against `windmill-worker-1/2` (stock image `ghcr.io/windmill-labs/windmill:main`)
and the host user manager (`hz-debian`, systemd 260, host uid 1000). The systemd
client tools come from nix (`nix develop <flake>#systemd`), not the image. The deploy
surface is
the per-worker quadlet rendered by `deploy/podman/install.sh`
(`~/.config/containers/systemd/windmill-worker-N.container`).

## The one fact everything else follows from

A `systemd-run --user` / `systemctl --user start` issued **inside** a worker
container does **not** run the payload in the container. The container is only the
**D-Bus client**; the unit is forked by the **host** user manager. Proven by a
transient unit that wrote its own identity to the bind-mounted shared dir:

| | worker container (the client) | the unit it started |
|---|---|---|
| hostname | `af018767f54d` (podman id) | **`hz-debian`** (the host) |
| uid | `0` (container root) | **`1000`** (host user) |
| mount namespace | `mnt:[4026534758]` | **`mnt:[4026531832]`** (host's) |
| `bun` on PATH | yes (base image) | **no** (host genuinely lacks it) |
| `qemu-system-x86_64` | no | **`/usr/bin/qemu-system-x86_64`** (host) |

Why it works at all: the quadlet mounts the host **user** bus socket
(`Volume=%t/bus:%t/bus`) and injects `DBUS_SESSION_BUS_ADDRESS=unix:path=%t/bus` +
`XDG_RUNTIME_DIR=%t` via `WHITELIST_ENVS`. Rootless podman maps container uid 0 →
host uid 1000, so D-Bus `EXTERNAL`/`SO_PEERCRED` auth accepts the connection as the
bus owner. `systemctl`, `busctl`, `systemd-run`, `loginctl`, `varlinkctl` come from
the nix `#systemd` devShell (`pkgs.systemd`, version-matched to the host), so the worker
runs the stock Windmill image with no distro systemd dependency.

## What is reachable from a worker

| capability | status | notes |
|---|---|---|
| host `systemd --user` (list/start/stop/show transient + template units) | **yes** | the qsu control path |
| `/nix/store` | **yes** | `Volume=/nix:/nix`; real shared mount, same path host↔container |
| `nix` CLI | yes (via `PATH=/nix/var/nix/profiles/default/bin`) | not on default PATH; flows export it |
| `WORKERS_DIR` artifacts | **yes, host-visible** | bind-mounted at the *same absolute path*; a host-forked QEMU reads them directly |
| host **system** bus (`hostnamectl`, `timedatectl`, `machinectl` system scope) | **no** | system bus socket not mounted (see below) |
| `hostname` (the command) | yes, but container-scoped | reads the container UTS ns; returns the podman id, not the host |
| `machinectl` | absent in base image | not needed for the user-scope qsu path; lives host-side anyway |

`SystemState=running` on the user manager (an early `is-system-running` →
`offline` was a spurious first-connect read; re-checks say running).

## Execution-model constraints (these dictate how qsu units must be written)

Because units fork on the host, in the host mount namespace, as host uid 1000:

1. **`ExecStart=` must resolve on the host.** Use a `/nix/store/...` path (resolves
   identically on host and container because `/nix` is the same mount); that is
   also the reproducible choice. The host distro `/usr/bin/qemu-system-x86_64`
   exists but is not reproducible; do not depend on it.
2. **Every path a unit touches must be host-valid.** Container-private paths
   (`/usr`, the container's `/tmp`, anything from the base image) are invisible to
   the host fork. Stay within the shared surfaces: `/nix/store` and `WORKERS_DIR`
   (`/home/dagomez/src/kdevops-ng/workers`).
3. **Ownership lines up.** The build writes as container-root → host uid 1000; the
   host-forked QEMU is uid 1000, so it reads build artifacts (the manifest's
   `bzImage`/`build_dir`) without a uid dance.
4. **Cross-worker build→boot works without `same_worker`.** A kernel built on
   worker `0000` lands under `WORKERS_DIR/0000/...`, which is a host bind-mount, so
   the host-forked QEMU can read it regardless of which worker issued the start.
   `same_worker` is only needed when *steps inside the container* must share files
   (the kernel build pipeline), not for VM boot.

## The established qsu pattern (from `~/src/linux-kdevops/refactor/kdevops`)

"qsu" = qemu-system-units (`scripts/qemu-system-units/`): each guest is a
`qemu-system@<vm>` systemd **user** service registered with systemd-machined:
"machinectl is to qsu what virsh is to libvirt". The live host already runs this
(`qemu-system@nixos.service`, `virtiofsd@nixos-*.service`).

- **Template units** (rendered from `scripts/qemu-system-units/templates/*.j2` into
  `~/.config/systemd/user/`): `qemu-system@.service`, `virtiofsd@.service`,
  `virtiofsd@.socket` (socket-activated, `StopWhenUnneeded=yes`).
- **Per-VM config**: `~/.config/systemd/qemu-system/<vm>.env`
  (`QEMU_ARGS`, `KERNEL_ARGS`, SSH/vsock ports = base+index) + drop-ins
  `~/.config/systemd/user/qemu-system@<vm>.service.d/override.conf` (vfio, vsock
  cid, virtiofsd `Requires=`).
- **Lifecycle**: `systemctl --user start|stop|restart qemu-system@<vm>`.
  `ExecStartPost=` registers with machined over **Varlink**
  (`varlinkctl call /run/user/%U/systemd/machine/io.systemd.Machine
  io.systemd.Machine.Register …`, `class=vm`, optional `vSockCid`). `ExecStop=`
  pipes a QMP `system_powerdown` via `socat` to `%t/qemu-system/%i/qmp.sock`
  (optional SSH `systemctl poweroff` first when a guest key is configured);
  `TimeoutStopSec=2min` then SIGKILL. `Slice=machine.slice`; hardened
  `DevicePolicy=closed` + `DeviceAllow=/dev/kvm`, `/dev/vhost-vsock`.
- **Destroy** (`playbooks/roles/qsu/tasks/destroy.yml`): stop instances, then
  remove the per-VM `.env`, drop-in dirs, and `~/.local/state/qemu-system/<vm>/`
  (the NVMe qcow2 backing files + runtime sockets). machined unregisters
  automatically on stop. There is no separate "destroy disks" beyond removing the
  state dir.

## Reproducibility plan (nix always; only the host *manager* is reused)

Goal: no distro-package / host-environment dependency. The only thing reused as-is
is the host's running `systemd --user` *manager* (it forks the units); every
*binary* (including the systemd client tools) comes from nix. Current distro
couplings in the past pattern and the fix:

| past dependency | reproducible replacement |
|---|---|
| QEMU = hand-built `kdevops/data/qemu-destdir/bin/qemu-system-x86_64` | **nix**: the flake already provides it (`pkgs.qemu`, `lib/toolchain.nix:qemu-utils`); render `ExecStart=` with the resolved `/nix/store/...` path |
| virtiofsd = distro `/usr/libexec/virtiofsd` | **nix**: flake already has `pkgs.virtiofsd` (`lib/toolchain.nix:37`, controller profile `vhostUserPackages = [ pkgs.virtiofsd ]`); the unit's `virtiofsd_binary` var is configurable, point it at the store path |
| `socat` (QMP power-down glue) | **nix** `pkgs.socat` store path, or replace with a tiny nix-built QMP client |
| `systemctl`/`systemd-run`/`varlinkctl`/`busctl` = distro `apt install systemd` in a custom image | **nix**: `pkgs.systemd` via the `#systemd` devShell (version-matched to the host manager); worker runs the stock Windmill image |
| `machinectl`, the user manager itself | **reused**: the host's running `systemd --user` forks the units; the client tools driving it are nix (above) |

The unit files themselves get rendered with store paths (e.g. by a
`f/vm/render_units` step or a nix derivation that writes the units). Because `/nix`
is shared at the same path, a store path chosen in the container is valid for the
host manager that execs it; this is what makes nix-provided QEMU/virtiofsd work
through host systemd at all.

## Dedicated VM worker: recommended

VM **visibility/tracking is already global**: every worker container talks to the
same host user manager, so `systemctl --user list-units` / `machinectl` see the
same VMs no matter who issued the call. A dedicated worker is therefore **not
required for tracking**.

It is still **recommended**, as a single worker in its own group/`tag` (e.g.
`vm`), for *operational* reasons:

- **Serialization**: one job at a time per worker naturally serializes
  start/stop/destroy, avoiding races on shared unit names, `.env` files and state
  dirs (the qsu artifacts are keyed by VM name, not by worker).
- **Routing**: `tag: vm` keeps VM steps off a worker mid-40-minute kernel build;
  build steps stay CPU-heavy, VM steps stay light/IO.
- **Ownership**: one place renders/installs the qsu units and owns teardown.

Mechanics: give the VM step a `tag` and run a worker in a matching group (the
flow-reference §"same_worker / tags"). It does **not** need to be the worker that
built the kernel (constraint #4 above), so build (group `default`) → boot (group
`vm`) composes cleanly across workers with plain `results.*` host paths; no
`same_worker`.

## hostname / hostnamectl / timedatectl: current status + the decision

In kdevops these are **host controller provisioning**, run with `become: true`
(root) in `playbooks/roles/devconfig`: write `/etc/hostname`, `timedatectl set-ntp
true`, `timedatectl status`. They are *not* guest config (guests set their own
hostname/time against the guest's systemd, which works natively in the guest).

From a worker container today:
- `hostname` **works** but is container-scoped (UTS namespace): returns the podman
  id; setting it would only affect the container. Not useful for host/guest config.
- `hostnamectl` / `timedatectl` **fail**: "Failed to connect to bus"; they need the
  **system** bus (`org.freedesktop.hostname1` / `timedate1`), and the quadlet mounts
  only the user bus. The host socket exists and is world-writable
  (`srw-rw-rw- /run/dbus/system_bus_socket`), so it *is* mountable.

To enable them, add to the worker quadlet (via `deploy/podman/install.sh`):
```
Volume=/run/dbus/system_bus_socket:/run/dbus/system_bus_socket
Environment=DBUS_SYSTEM_BUS_ADDRESS=unix:path=/run/dbus/system_bus_socket
# and add DBUS_SYSTEM_BUS_ADDRESS to WHITELIST_ENVS
```
Caveats before doing this:
- **GET works, SET needs privilege.** Reading status would work as uid 1000;
  `set-hostname` / `set-ntp` go through polkit and will be denied
  non-interactively unless a polkit rule allows uid 1000.
- **It widens the host attack surface**: the system bus exposes *every* system
  service to the container, not just hostnamed/timedated. That fights the
  reproducibility/isolation posture.
- **It mutates the host.** Setting the Windmill host's hostname/time from a flow is
  a host-management action, semantically different from per-VM provisioning.

Recommendation: do **not** mount the system bus by default. If host
hostname/NTP control is genuinely needed from a flow, scope it to the dedicated VM
worker and gate set-operations behind an explicit polkit rule. For *guest*
hostname/time, do it inside the guest (cloud-init / a guest systemd unit), where it
needs no host-bus access.

## Open decisions for the qsu build phase

1. Dedicated `vm`-tagged worker: recommended; confirm and wire the group in
   `deploy/podman/install.sh`.
2. System bus for hostnamectl/timedatectl: default **no**; revisit only if a flow
   must manage the *host's* hostname/time.
3. Render qsu units with nix store paths for QEMU + virtiofsd (+ socat); keep
   varlinkctl/machinectl as the systemd exception.

## Windmill implementation (`f/qsu`): verified findings (2026-06-09)

The flow `f/qsu/boot` (`render_qemu_system → render_virtiofsd → create_nvme → boot`)
plus `stop`/`destroy`/`status` is live; a `demo`-style VM was booted to
machined-registered + sshd-serving and destroyed clean. Four things only the live
host revealed:

1. **`systemctl --user` must use the D-Bus bus, not the private socket.** From a
   rootless-podman worker, `systemctl`/`machinectl` default to the manager's
   `$XDG_RUNTIME_DIR/systemd/private` socket, whose connection the host manager
   **refuses across the podman namespaces** ("No data available") even though uid maps
   0→1000. The mounted D-Bus bus *is* accepted (so `systemd-run`/`busctl`/`varlinkctl`
   work). Fix: run them with **`env --unset=XDG_RUNTIME_DIR`** so they fall back to
   `DBUS_SESSION_BUS_ADDRESS` (baked into `f.common.devshell.Systemd`). No
   private-socket mount needed; the worker mounts only `%t/bus` + the host
   `~/.config/systemd/{user,qemu-system,virtiofsd}` + `~/.local/state/qemu-system`.
2. **Route per-step, not `same_worker`.** `same_worker: true` pins a flow to whatever
   worker runs it and bypasses per-step tags, landing the renders on a build worker
   without the host-config mounts. Drop `same_worker`; tag every step (and every
   standalone qsu script's `.script.yaml`) `vm`, and give the dedicated worker
   `WORKER_TAGS=vm` (group alone does not select tags; `default` is otherwise a
   catch-all for unrouted tags).
3. **Guest-readiness can't be probed from the worker.** The forwarded SSH port lives on
   the *host's* loopback; an in-container probe of `127.0.0.1:<ssh_port>` hits the
   container's own loopback. `boot` therefore reports `active` from `systemctl
   is-active` (over the bus) and treats the SSH banner as best-effort `ssh_ready`;
   verify the guest from the host or over vsock.
4. **The host's `~/.config/systemd/user` is shared with the operator's kdevops VMs.**
   The vm worker writes into the same dir the kdevops ansible qsu role uses, so the
   shared `qemu-system@.service`/`virtiofsd@.service` templates and per-VM names must
   not collide; use distinct vm names and keep the binaries matching.
