#!/usr/bin/env bash
# SPDX-License-Identifier: copyleft-next-0.3.1
# Provision the host-sourced parts of the shared workspace under workers/shared
# (idempotent). The kernel and qemu sources are NO LONGER cloned here — the
# `f/workspace/init` flow (f/workspace/fetch) owns mirror provisioning now,
# including upstream tracking and the extra kernel remotes. nixos-flake and
# qemu-system-units are now vendored as git subtrees (tracked in the repo), so
# they are not provisioned here. What remains:
#   linux-config-fragments -> curated kernel config fragments, copied for now
#                   (a git subtree later), used by the fragment config steps.
# Per-worker sandboxes workers/<NNNN> are created by install.sh.
set -o errexit -o nounset -o pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
SHARED="$(cd "$HERE/../.." && pwd)/workers/shared"
CONFIG_FRAGMENTS="${CONFIG_FRAGMENTS:-$HOME/src/linux-config-fragments}"

mkdir --parents "$SHARED"
if [ ! -e "$SHARED/linux-config-fragments/kernel/configs" ]; then
		cp --archive "$CONFIG_FRAGMENTS" "$SHARED/linux-config-fragments"
fi
echo "shared workspace (fragments) ready at $SHARED"
