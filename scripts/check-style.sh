#!/usr/bin/env bash
# SPDX-License-Identifier: copyleft-next-0.3.1
# Style + commit-message checks for kdevops-ng. Run from the checks devShell:
#   nix develop .#checks --command bash scripts/check-style.sh
# Hand-authored files only: machine-generated workspace content (f/, wmill-lock.yaml)
# is owned by wmill, the vendored git subtrees under vendor/ are upstream-owned, the
# verbatim license texts under LICENSES/ must stay byte-for-byte,
# scripts/get_maintainer.pl is vendored from the kernel and tracked verbatim, and
# screenshots/ and docs/_static/ hold binary image artifacts (no text style
# applies); all exempt.
set -o errexit -o nounset -o pipefail

scope=(-- . ':!f' ':!workers' ':!vendor' ':!LICENSES' ':!wmill-lock.yaml' ':!scripts/get_maintainer.pl' ':!screenshots' ':!docs/_static')
status=0

# 1. Trailing whitespace.
if git grep --line-number --perl-regexp ' +$' "${scope[@]}" >/dev/null 2>&1; then
		echo "error: trailing whitespace:"
		git grep --line-number --perl-regexp ' +$' "${scope[@]}"
		status=1
fi

# 2. Missing newline at end of file.
while IFS= read -r f; do
		[ -s "$f" ] || continue
		if [ -n "$(tail --bytes=1 "$f")" ]; then
				echo "error: no newline at end of file: $f"
				status=1
		fi
done < <(git ls-files "${scope[@]}")

# 3. HEAD commit message: Signed-off-by present, and Generated-by (if any)
#    immediately followed by Signed-off-by with no blank line between.
msg="$(git log --max-count=1 --format=%B 2>/dev/null || true)"
if [ -n "$msg" ]; then
		if printf '%s\n' "$msg" | grep --quiet '^Generated-by:'; then
				if ! printf '%s\n' "$msg" | grep --after-context=1 '^Generated-by:' \
						| grep --quiet '^Signed-off-by:'; then
						echo "error: HEAD: 'Generated-by:' must be immediately followed by 'Signed-off-by:'"
						status=1
				fi
		fi
		if ! printf '%s\n' "$msg" | grep --quiet '^Signed-off-by:'; then
				echo "error: HEAD: missing Signed-off-by trailer"
				status=1
		fi
fi

# 4. reStructuredText line length under docs/: prose wraps at 80 columns.
#    URLs cannot be broken, so a line that contains one is exempt.
while IFS= read -r f; do
	python3 - "$f" <<'PY' || status=1
import sys

path = sys.argv[1]
bad = False
with open(path, encoding="utf-8") as handle:
    for num, line in enumerate(handle, 1):
        text = line.rstrip("\n")
        if len(text) > 80 and "://" not in text:
            print(f"error: {path}:{num}: line exceeds 80 columns ({len(text)})")
            bad = True
sys.exit(1 if bad else 0)
PY
done < <(git ls-files docs | grep --extended-regexp '\.rst$' || true)

# 5. reStructuredText under docs/ must declare an SPDX license on line one.
while IFS= read -r f; do
	case "$(head --lines=1 "$f")" in
	".. SPDX-License-Identifier: "*) ;;
	*)
		echo "error: $f: missing '.. SPDX-License-Identifier:' on the first line"
		status=1
		;;
	esac
done < <(git ls-files docs | grep --extended-regexp '\.rst$' || true)

[ "$status" -eq 0 ] && echo "style: OK"
exit "$status"
