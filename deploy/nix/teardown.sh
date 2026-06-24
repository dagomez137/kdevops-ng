#!/usr/bin/env bash
# SPDX-License-Identifier: copyleft-next-0.3.1
# Stop and remove the nix + systemd --user Windmill units. The state (the
# postgres cluster, the generated secret, the env file) under
# $XDG_STATE_HOME/windmill-nix is kept by default; pass --purge to wipe it.
set -o errexit -o nounset -o pipefail
STATE="${XDG_STATE_HOME:-$HOME/.local/state}/windmill-nix"
UNITS="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

# Stop every windmill user unit (server, db, native, and the worker template
# instances) and remove the rendered unit files.
mapfile -t units < <(systemctl --user list-units --all --plain --no-legend \
    'windmill*' 2>/dev/null | awk '{print $1}')
[ "${#units[@]}" -gt 0 ] && systemctl --user stop "${units[@]}" 2>/dev/null || true
rm --force "$UNITS"/windmill.service "$UNITS"/windmill-db.service \
    "$UNITS"/windmill-native.service "$UNITS"/windmill-caddy.service \
    "$UNITS"/windmill-worker@.service "$UNITS"/windmill-worker-vm@.service \
    "$UNITS"/windmill-worker-vmrun@.service
systemctl --user daemon-reload
echo "stopped and removed units"

if [ "${1:-}" = "--purge" ]; then
    rm --recursive --force "$STATE"
    echo "purged state at $STATE (database wiped)"
else
    echo "state kept at $STATE (the database survives; rerun with --purge to wipe)"
fi
