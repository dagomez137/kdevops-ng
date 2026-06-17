#!/usr/bin/env bash
# SPDX-License-Identifier: copyleft-next-0.3.1
# Stop + destroy the instance and ALL its runtime state. Repo files untouched.
# Images are kept (cache). Add `podman rmi ...` if you want them gone too.
# Matches every windmill* unit/container so the worker pool count does not matter.
set -o nounset
STATE="${XDG_STATE_HOME:-$HOME/.local/state}/windmill"
CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/windmill"
UNITS="${XDG_CONFIG_HOME:-$HOME/.config}/containers/systemd"
systemctl --user stop 'windmill*.service' 2>/dev/null || true
rm --force "$UNITS"/windmill*.container "$UNITS"/windmill.network
systemctl --user daemon-reload
systemctl --user reset-failed 'windmill*' 2>/dev/null || true
podman ps --all --format '{{.Names}}' | grep '^windmill' \
		| xargs --no-run-if-empty podman rm --force 2>/dev/null || true
podman network rm --force windmill 2>/dev/null || true
# the db dir is chowned to a high subuid by the :U mount -> delete inside the userns
podman unshare rm --recursive --force "$STATE" "$CACHE" 2>/dev/null \
		|| rm --recursive --force "$STATE" "$CACHE"
rm --recursive --force "${XDG_CONFIG_HOME:-$HOME/.config}/windmill"
echo "torn down + data and wmill CLI profile wiped."
