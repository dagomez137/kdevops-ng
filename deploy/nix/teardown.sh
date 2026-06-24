#!/usr/bin/env bash
# SPDX-License-Identifier: copyleft-next-0.3.1
# Stop and remove the nix + systemd --user Windmill units. The state (the
# postgres cluster, the generated secret, the env file) under
# $XDG_STATE_HOME/windmill-nix is kept by default; pass --purge to wipe it.
set -o errexit -o nounset -o pipefail
STATE="${XDG_STATE_HOME:-$HOME/.local/state}/windmill-nix"
UNITS="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

systemctl --user stop windmill.service windmill-db.service 2>/dev/null || true
rm --force "$UNITS"/windmill.service "$UNITS"/windmill-db.service
systemctl --user daemon-reload
echo "stopped and removed units"

if [ "${1:-}" = "--purge" ]; then
    rm --recursive --force "$STATE"
    echo "purged state at $STATE (database wiped)"
else
    echo "state kept at $STATE (the database survives; rerun with --purge to wipe)"
fi
