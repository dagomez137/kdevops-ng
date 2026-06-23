#!/usr/bin/env bash
# SPDX-License-Identifier: copyleft-next-0.3.1
# Bring up the rootless-podman Windmill instance from this repo. Idempotent.
# Renders WORKERS general worker replicas, so up to WORKERS jobs (e.g. kernel
# builds) run at once; override with `WORKERS=N`. Host paths use systemd
# specifiers (XDG-first): %S state, %C cache; /nix is bind-mounted for building.
set -o errexit -o nounset -o pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
# Build-area layout (ADR-0008). WORKBENCH_DIR is the whole relocatable build area;
# the System workbench (SYSTEM_DIR: bare/ mirror/ ssh/ store/) and the per-worker
# sandbox root (WORKERS_DIR) default under it but each relocates on its own. Set any
# of them to override.
WORKBENCH_DIR="${WORKBENCH_DIR:-$REPO/workbench}"
SYSTEM_DIR="${SYSTEM_DIR:-$WORKBENCH_DIR/system}"
WORKERS_DIR="${WORKERS_DIR:-$WORKBENCH_DIR/workers}"
STATE="${XDG_STATE_HOME:-$HOME/.local/state}/windmill"
CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/windmill"
UNITS="${XDG_CONFIG_HOME:-$HOME/.config}/containers/systemd"
WORKERS="${WORKERS:-2}"
# Two vm-group worker pools, split by operation class so a long fstests poll can
# never starve a quick lifecycle op. `vm` workers run the control/lifecycle steps
# (boot/stop/destroy/status/discover/render/prepare/start/collect/report) and must
# stay responsive; `vm-run` workers run only the long-lived fstests `wait` poll, so
# their count is the concurrent-test-run cap.
VM_WORKERS="${VM_WORKERS:-4}"
VM_RUN_WORKERS="${VM_RUN_WORKERS:-3}"
# Vendored projects (ADR-0006) live in the top-level vendor/, a sibling of the
# Workbench; every worker bind-mounts it read-only at the same absolute path.
VENDOR_DIR="${VENDOR_DIR:-$(dirname "$WORKBENCH_DIR")/vendor}"

# The per-worker identity of the i-th vm control worker: the first stays `vm` (its
# sandbox dir, WORKER_INDEX and container name are unchanged), the rest are
# `vm2`, `vm3`, ... so they coexist without colliding on the per-worker dir.
_vmself() { [ "$1" = 1 ] && echo vm || echo "vm$1"; }

# Render one vm-group worker unit: $1 = WORKER_TAGS, $2 = per-worker identity.
_render_vm_worker() {
	mkdir --parents "$WORKERS_DIR/$2"
	sed --expression "s|@WORKBENCH_DIR@|$WORKBENCH_DIR|g" \
			--expression "s|@SYSTEM_DIR@|$SYSTEM_DIR|g" \
			--expression "s|@WORKERS_DIR@|$WORKERS_DIR|g" \
			--expression "s|@VENDOR_DIR@|$VENDOR_DIR|g" \
			--expression "s|@SECCOMP_PROFILE@|$SECCOMP|g" \
			--expression "s|@VMTAG@|$1|g" \
			--expression "s|@VMSELF@|$2|g" \
			"$HERE/windmill-worker-vm.container.tmpl" >"$UNITS/windmill-worker-$2.container"
}

loginctl enable-linger "$USER" 2>/dev/null || true
mkdir --parents "$STATE/db" "$CACHE/cache" "$CACHE/lsp" "$CACHE/logs" "$UNITS"
install --mode=644 "$HERE/Caddyfile" "$STATE/Caddyfile"

install --mode=644 "$HERE"/systemd/*.network "$HERE"/systemd/*.container "$UNITS"/

# Render WORKERS worker replicas (idempotent: clear stale ones first). Each
# worker gets a numbered sandbox dir workers/w<NNNN> (0-based, zero-padded) under
# the worker-sandbox root. The shared/ tree holds runtime caches (ccache, source
# mirrors of the test suites); the repo-tracked vendored deps (nixos-flake,
# qemu-system-units, linux-config-fragments) live in the top-level vendor/
# (ADR-0006); the System workbench (SYSTEM_DIR) holds the durable Bare every build
# worktree hangs off; the f/workbench setup flow provisions the runtime bits (the
# Bare, SSH key) once Windmill is up.
mkdir --parents "$WORKERS_DIR/shared" "$SYSTEM_DIR"
rm --force "$UNITS"/windmill-worker-*.container
for i in $(seq 1 "$WORKERS"); do
		self=$(printf 'w%04d' "$((i - 1))")
		mkdir --parents "$WORKERS_DIR/$self"
		sed --expression "s|@INDEX@|$i|g" \
				--expression "s|@SELFDIR@|$self|g" \
				--expression "s|@WORKBENCH_DIR@|$WORKBENCH_DIR|g" \
				--expression "s|@SYSTEM_DIR@|$SYSTEM_DIR|g" \
				--expression "s|@WORKERS_DIR@|$WORKERS_DIR|g" \
				--expression "s|@VENDOR_DIR@|$VENDOR_DIR|g" \
				"$HERE/windmill-worker.container.tmpl" >"$UNITS/windmill-worker-$i.container"
done

# The vm workers (group vm) run the f/qsu/* steps. The host user's systemd search
# path + VM state dirs must exist first (the qsu render steps write units here for
# the host manager to fork; shared by every vm worker). Each worker's own sandbox
# dir is created by _render_vm_worker below.
mkdir --parents "${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user" \
		"${XDG_CONFIG_HOME:-$HOME/.config}/systemd/qemu-system" \
		"${XDG_CONFIG_HOME:-$HOME/.config}/systemd/virtiofsd" \
		"${XDG_STATE_HOME:-$HOME/.local/state}/qemu-system"
# The vm worker reaches booted guests over AF_VSOCK, which needs the host's
# vhost_vsock module loaded (it creates /dev/vsock). Loading a module is the one
# step that needs root in an otherwise rootless install; persist it across reboots
# via /etc/modules-load.d. Idempotent: skipped once /dev/vsock exists.
if [ ! -e /dev/vsock ]; then
	sudo modprobe vhost_vsock
	echo vhost_vsock | sudo tee /etc/modules-load.d/vhost_vsock.conf >/dev/null
fi
# Beyond the module, the vm worker drives guests over vsock (`systemctl --host
# root@vsock/<cid>`), which needs the AF_VSOCK socket family the default podman
# seccomp profile blocks. Derive a least-privilege profile from the host's own
# default by flipping only the one rule that denies `socket(AF_VSOCK)`; every
# other syscall keeps the host default, and the profile tracks the host podman
# version (no vendored copy to go stale).
SECCOMP="$UNITS/windmill-worker-vm-seccomp.json"
DEFAULT_SECCOMP="${PODMAN_SECCOMP:-/usr/share/containers/seccomp.json}"
python3 - "$DEFAULT_SECCOMP" "$SECCOMP" <<'PY'
import json, sys
src, dst = sys.argv[1], sys.argv[2]
prof = json.load(open(src))
flipped = 0
for s in prof.get("syscalls", []):
    if "socket" in s.get("names", []) and s.get("action") == "SCMP_ACT_ERRNO":
        for a in s.get("args", []):
            if a.get("index") == 0 and a.get("value") == 40 and a.get("op") == "SCMP_CMP_EQ":
                s["action"] = "SCMP_ACT_ALLOW"
                s.pop("errnoRet", None); s.pop("errno", None)
                flipped += 1
if flipped != 1:
    sys.exit(f"{src}: expected one AF_VSOCK socket rule to flip, found {flipped}")
json.dump(prof, open(dst, "w"), indent=2)
print(f"derived {dst} (host default + AF_VSOCK allowed)")
PY

# Peer workbench hosts the vm workers sweep for cross-host VM discovery
# (f.qsu.common.vm_options lists `qemu-system@*` over ssh). PEERS is a
# space-separated list of ssh-config host aliases of OTHER workbench hosts (never
# self). The worker has no ~/.ssh, so the sweep runs `ssh -F $SYSTEM_DIR/ssh/config`:
# resolve each alias from the operator's ssh config here (host-side) into a Host
# block that points at one dedicated, least-privilege peer key, and record the
# alias in $SYSTEM_DIR/peers (the registry vm_options reads). The peer key's public
# half must be authorized on each peer's authorized_keys out of band. A re-run with
# PEERS unset preserves the existing registry, so the rewrite never silently drops
# a peer (set PEERS explicitly to change the set).
PEERS="${PEERS:-$(cat "$SYSTEM_DIR/peers" 2>/dev/null || true)}"
SSH_DIR="$SYSTEM_DIR/ssh"
PEER_KEY="$SSH_DIR/peer_ed25519"
mkdir --parents "$SSH_DIR/config.d"
[ -f "$PEER_KEY" ] || ssh-keygen -t ed25519 -N "" -C kdevops-workbench-peer -f "$PEER_KEY"
# f/workbench/ssh_key rewrites $SYSTEM_DIR/ssh/config later; seed the Include so a
# sweep resolves config.d/peers.conf even before the first workbench init.
grep -qs 'config.d/\*.conf' "$SSH_DIR/config" 2>/dev/null \
		|| printf 'Include %s/config.d/*.conf\n' "$SSH_DIR" >"$SSH_DIR/config"
: >"$SSH_DIR/config.d/peers.conf"
: >"$SYSTEM_DIR/peers"
for peer in $PEERS; do
		g=$(ssh -G "$peer")
		ph=$(printf '%s\n' "$g" | awk '/^hostname /{print $2; exit}')
		pp=$(printf '%s\n' "$g" | awk '/^port /{print $2; exit}')
		pu=$(printf '%s\n' "$g" | awk '/^user /{print $2; exit}')
		cat >>"$SSH_DIR/config.d/peers.conf" <<EOF
Host $peer
    HostName $ph
    Port $pp
    User $pu
    IdentityFile $PEER_KEY
    IdentitiesOnly yes
    StrictHostKeyChecking accept-new
    UserKnownHostsFile $SSH_DIR/known_hosts
EOF
		printf '%s\n' "$peer" >>"$SYSTEM_DIR/peers"
		ssh-keyscan -p "$pp" "$ph" >>"$SSH_DIR/known_hosts" 2>/dev/null || true
		echo "peer $peer -> $pu@$ph:$pp (authorize $PEER_KEY.pub on $peer)"
done
# OpenSSH refuses a group/world-writable config (the default umask makes the
# heredoc above 0664), so the sweep would fail with "bad owner or permissions".
chmod go-w "$SSH_DIR/config" "$SSH_DIR/config.d/peers.conf" 2>/dev/null || true

# Control/lifecycle pool (tag vm), then the long-poll pool (tag vm-run).
for i in $(seq 1 "$VM_WORKERS"); do _render_vm_worker vm "$(_vmself "$i")"; done
for i in $(seq 1 "$VM_RUN_WORKERS"); do _render_vm_worker vm-run "vmrun$i"; done

systemctl --user daemon-reload
systemctl --user start windmill-caddy.service windmill-native.service
# Restart, not start: a re-render changes the worker quadlets (mounts, WORKER_INDEX),
# and an already-running container keeps its old config until it is restarted. On a
# first run the units are inactive and restart simply starts them.
for i in $(seq 1 "$WORKERS"); do
		systemctl --user restart "windmill-worker-$i.service"
done
for i in $(seq 1 "$VM_WORKERS"); do
		systemctl --user restart "windmill-worker-$(_vmself "$i").service"
done
for i in $(seq 1 "$VM_RUN_WORKERS"); do
		systemctl --user restart "windmill-worker-vmrun$i.service"
done
echo "up -> http://127.0.0.1:8000 with $WORKERS build + $VM_WORKERS vm + $VM_RUN_WORKERS vm-run worker(s)  (ssh -L 8000:localhost:8000)"
