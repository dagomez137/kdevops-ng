#!/usr/bin/env bash
# SPDX-License-Identifier: copyleft-next-0.3.1
# Style + commit-message checks for kdevops-ng. Invoked by `make style`.
# Hand-authored files only: machine-generated workspace content (f/, wmill-lock.yaml)
# is owned by wmill, the vendored git subtrees under workers/ are upstream-owned, the
# verbatim license texts under LICENSES/ must stay byte-for-byte,
# scripts/get_maintainer.pl is vendored from the kernel and tracked verbatim, and
# screenshots/ holds binary image artifacts (no text style applies); all exempt.
set -o errexit -o nounset -o pipefail

scope=(-- . ':!f' ':!workers' ':!LICENSES' ':!wmill-lock.yaml' ':!scripts/get_maintainer.pl' ':!screenshots')
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

[ "$status" -eq 0 ] && echo "style: OK"
exit "$status"
