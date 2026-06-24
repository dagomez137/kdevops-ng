#!/usr/bin/env bash
# SPDX-License-Identifier: copyleft-next-0.3.1
# Bring up the nix + systemd --user Windmill instance from this flake. Idempotent.
#
# Each component is built to a GC-rooted out-link under the user state dir, so
# the binaries the units exec survive `nix store gc` and live at a stable path
# independent of the store hash. The units reference that path as @SW@, which
# this script substitutes when it renders them into the user unit directory.
#
# Brings up the database, the server, and the worker pools. The vm and vm-run
# pools default off (they need the workbench provisioned); set VM_WORKERS and
# VM_RUN_WORKERS to start them. caddy and the LSP gateway are added later.
set -o errexit -o nounset -o pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
STATE="${XDG_STATE_HOME:-$HOME/.local/state}/windmill-nix"
SW="$STATE/sw"
UNITS="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

# Worker pool sizes. One native worker is always started.
WORKERS="${WORKERS:-2}"
VM_WORKERS="${VM_WORKERS:-0}"
VM_RUN_WORKERS="${VM_RUN_WORKERS:-0}"

# Build area (ADR-0008), defaulting under the repo like the podman backend.
# These are plain host paths now (no bind-mounts), passed to the build and vm
# workers through workbench.env.
WORKBENCH_DIR="${WORKBENCH_DIR:-$REPO/workbench}"
SYSTEM_DIR="${SYSTEM_DIR:-$WORKBENCH_DIR/system}"
WORKERS_DIR="${WORKERS_DIR:-$WORKBENCH_DIR/workers}"
VENDOR_DIR="${VENDOR_DIR:-$(dirname "$WORKBENCH_DIR")/vendor}"

COMPONENTS=(windmill postgresql db-setup)

echo "== build components to GC-rooted out-links under $SW =="
mkdir --parents "$SW"
for pkg in "${COMPONENTS[@]}"; do
    nix build "$HERE#$pkg" --out-link "$SW/$pkg"
    echo "  $pkg -> $(readlink "$SW/$pkg")"
done

echo "== prepare host state =="
mkdir --parents "$STATE/pgdata" "$STATE/secrets" "$STATE/env" "$WORKERS_DIR" "$SYSTEM_DIR"
chmod 700 "$STATE/secrets"
# Run user services without an active login session, as the podman backend does.
loginctl enable-linger "$USER" >/dev/null 2>&1 || true
# The build-area env the worker units read, written from its one true source here.
{
    printf 'WORKBENCH_DIR=%s\n' "$WORKBENCH_DIR"
    printf 'SYSTEM_DIR=%s\n' "$SYSTEM_DIR"
    printf 'WORKERS_DIR=%s\n' "$WORKERS_DIR"
    printf 'VENDOR_DIR=%s\n' "$VENDOR_DIR"
} >"$STATE/env/workbench.env"

echo "== render units into $UNITS =="
mkdir --parents "$UNITS"
for u in "$HERE"/systemd/*.service; do
    sed "s|@SW@|$SW|g" "$u" >"$UNITS/$(basename "$u")"
    echo "  $(basename "$u")"
done

echo "== bring up (restart picks up any re-render) =="
systemctl --user daemon-reload
# Type=notify: the start returns only once postgres is ready and ExecStartPost
# has written the DATABASE_URL env file the rest read.
systemctl --user restart windmill-db.service
systemctl --user restart windmill.service
systemctl --user restart windmill-native.service

# Stop any worker instances from a previous run so a reduced count takes effect,
# then start the requested set. Template instances are addressed by index: the
# build pool is 0-based, the vm and vm-run pools are 1-based.
mapfile -t stale < <(systemctl --user list-units --all --plain --no-legend \
    'windmill-worker@*' 'windmill-worker-vm@*' 'windmill-worker-vmrun@*' 2>/dev/null | awk '{print $1}')
[ "${#stale[@]}" -gt 0 ] && systemctl --user stop "${stale[@]}" 2>/dev/null || true

start_pool() { # $1 template base, $2 count, $3 first index
    local base="$1" n="$2" first="$3" i
    for ((i = 0; i < n; i++)); do
        systemctl --user restart "${base}@$((first + i)).service"
    done
}
start_pool windmill-worker "$WORKERS" 0
start_pool windmill-worker-vm "$VM_WORKERS" 1
start_pool windmill-worker-vmrun "$VM_RUN_WORKERS" 1

echo "up -> server http://127.0.0.1:8002"
echo "      workers: 1 native + $WORKERS build + $VM_WORKERS vm + $VM_RUN_WORKERS vm-run"
echo "      systemctl --user status windmill windmill-native 'windmill-worker@*'"
