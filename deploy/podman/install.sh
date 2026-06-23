#!/usr/bin/env bash
# SPDX-License-Identifier: copyleft-next-0.3.1
# Bring up the rootless-podman Windmill instance from this repo. Idempotent.
# Renders WORKERS general worker replicas, so up to WORKERS jobs (e.g. kernel
# builds) run at once; override with `WORKERS=N`. Host paths use systemd
# specifiers (XDG-first): %S state, %C cache; /nix is bind-mounted for building.
set -o errexit -o nounset -o pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
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

# The per-worker identity of the i-th vm control worker: the first stays `vm` (its
# sandbox dir, WORKER_INDEX and container name are unchanged), the rest are
# `vm2`, `vm3`, ... so they coexist without colliding on the per-worker dir.
_vmself() { [ "$1" = 1 ] && echo vm || echo "vm$1"; }

# Render one vm-group worker unit: $1 = WORKER_TAGS, $2 = per-worker identity.
_render_vm_worker() {
	mkdir --parents "$WORKERS_DIR/$2"
	sed --expression "s|@WORKERS_DIR@|$WORKERS_DIR|g" \
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
# worker gets a numbered sandbox dir workers/w<NNNN> (0-based, zero-padded). The
# shared/ tree carries the repo-tracked vendored deps (nixos-flake,
# qemu-system-units, linux-config-fragments); the system/ tree holds the durable
# Bare every build worktree hangs off; the f/workspace setup flow provisions the
# runtime bits (the Bare, SSH key) once Windmill is up.
mkdir --parents "$WORKERS_DIR/shared" "$WORKERS_DIR/system"
rm --force "$UNITS"/windmill-worker-*.container
for i in $(seq 1 "$WORKERS"); do
		self=$(printf 'w%04d' "$((i - 1))")
		mkdir --parents "$WORKERS_DIR/$self"
		sed --expression "s|@INDEX@|$i|g" \
				--expression "s|@SELFDIR@|$self|g" \
				--expression "s|@WORKERS_DIR@|$WORKERS_DIR|g" \
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
# The vm worker drives booted guests over vsock (`systemctl --host
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
