#!/usr/bin/env bash
# SPDX-License-Identifier: copyleft-next-0.3.1
# Bring up the nix + systemd --user Windmill instance from this flake. Idempotent.
#
# Each component is built to a GC-rooted out-link under the user state dir, so
# the binaries the units exec survive `nix store gc` and live at a stable path
# independent of the store hash. The units reference that path as @SW@, which
# this script substitutes when it renders them into the user unit directory.
#
# Currently brings up the database and the server; the worker groups, caddy and
# the LSP gateway are added as their units land.
set -o errexit -o nounset -o pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
STATE="${XDG_STATE_HOME:-$HOME/.local/state}/windmill-nix"
SW="$STATE/sw"
UNITS="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

# Flake packages each unit's @SW@/<name> path resolves to. Grows with the stack.
COMPONENTS=(windmill postgresql db-setup)

echo "== build components to GC-rooted out-links under $SW =="
mkdir --parents "$SW"
for pkg in "${COMPONENTS[@]}"; do
    nix build "$HERE#$pkg" --out-link "$SW/$pkg"
    echo "  $pkg -> $(readlink "$SW/$pkg")"
done

echo "== prepare host state =="
mkdir --parents "$STATE/pgdata" "$STATE/secrets" "$STATE/env"
chmod 700 "$STATE/secrets"
# Run user services without an active login session, as the podman backend does.
loginctl enable-linger "$USER" >/dev/null 2>&1 || true

echo "== render units into $UNITS =="
mkdir --parents "$UNITS"
for u in "$HERE"/systemd/*.service; do
    sed "s|@SW@|$SW|g" "$u" >"$UNITS/$(basename "$u")"
    echo "  $(basename "$u")"
done

echo "== bring up (restart picks up any re-render) =="
systemctl --user daemon-reload
# Type=notify: the start returns only once postgres is ready and ExecStartPost
# has written the DATABASE_URL env file the server reads.
systemctl --user restart windmill-db.service
systemctl --user restart windmill.service

echo "up -> windmill server on http://127.0.0.1:8002 (caddy front + LSP added later)"
echo "      systemctl --user status windmill-db windmill"
