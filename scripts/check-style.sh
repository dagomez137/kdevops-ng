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

# Commit message trailers, as the kernel's checkpatch.pl checks them: a
# Signed-off-by is present and the last one is the committer's, an Assisted-by
# (if any) comes before one, and every Co-developed-by is immediately followed
# by the same person's Signed-off-by. Only the trailer block counts, read as
# `git interpret-trailers` reads it (without its patch divider, so a quoted
# hunk header in the body hides nothing), so a body line that quotes a tag is
# prose; tags match case-insensitively, as checkpatch matches them. A
# Generated-by is refused: Assisted-by replaced it.
check_trailers() {
		local where="$1" msg="$2" committer="$3" trailers last
		trailers="$(printf '%s\n' "$msg" | git interpret-trailers --parse --no-divider)"
		if ! printf '%s\n' "$trailers" | awk '
			tolower($0) ~ /^assisted-by:/ { assisted = 1 }
			tolower($0) ~ /^signed-off-by:/ && assisted { assisted = 0 }
			END { exit assisted }'; then
				echo "error: $where: 'Assisted-by:' must come before a 'Signed-off-by:'"
				status=1
		fi
		if ! printf '%s\n' "$trailers" | where="$where" committer="$committer" awk '
			pending {
				if (tolower($0) != ("signed-off-by: " tolower(person))) {
					print "error: " ENVIRON["where"] ": \"Co-developed-by: " person "\" must be immediately followed by \"Signed-off-by: " person "\""
					bad = 1
				}
				pending = 0
			}
			tolower($0) ~ /^co-developed-by:/ {
				person = $0
				sub(/^[^:]*:[[:space:]]*/, "", person)
				if (person == "") {
					print "error: " ENVIRON["where"] ": empty Co-developed-by"
					bad = 1
					next
				}
				if (tolower(person) == tolower(ENVIRON["committer"])) {
					print "error: " ENVIRON["where"] ": Co-developed-by names the committer, the primary author"
					bad = 1
				}
				pending = 1
			}
			END {
				if (pending) {
					print "error: " ENVIRON["where"] ": \"Co-developed-by: " person "\" has no Signed-off-by after it"
					bad = 1
				}
				exit bad
			}'; then
				status=1
		fi
		last="$(printf '%s\n' "$trailers" | grep --ignore-case '^Signed-off-by:' \
				| tail --lines=1 | sed 's/^[^:]*:[[:space:]]*//' || true)"
		if printf '%s\n' "$trailers" | grep --quiet --ignore-case '^Signed-off-by:[[:space:]]*$'; then
				echo "error: $where: empty Signed-off-by"
				status=1
		fi
		if printf '%s\n' "$trailers" | grep --quiet --ignore-case '^Generated-by:'; then
				echo "error: $where: 'Generated-by:' is replaced by 'Assisted-by: LLM'"
				status=1
		fi
		if [ -z "$last" ]; then
				echo "error: $where: missing Signed-off-by trailer"
				status=1
		elif [ "${last,,}" != "${committer,,}" ]; then
				echo "error: $where: the last Signed-off-by must be the committer's ($committer), not: $last"
				status=1
		fi
}

# `--commit-msg FILE` checks the trailers of the message in FILE and nothing
# else: the commit-msg hook's shape (scripts/commit-msg), so a bad message is
# refused before the commit exists rather than reported from HEAD afterwards.
# A verbose commit template carries the diff below git's scissors line, which
# stripspace keeps, so the file is cut there first, as git itself cuts it.
if [ "${1-}" = "--commit-msg" ]; then
		[ $# -eq 2 ] && [ -r "$2" ] || { echo "usage: $0 [--commit-msg FILE]" >&2; exit 2; }
		committer="$(git var GIT_COMMITTER_IDENT | sed 's/ [0-9]* [-+][0-9]*$//')"
		msg="$(sed '/^[#;] -* >8 -*$/,$d' "$2" | git stripspace --strip-comments)"
		check_trailers "commit message" "$msg" "$committer"
		exit "$status"
fi


# 1. Trailing whitespace (spaces or tabs) and carriage returns.
if git grep --line-number --perl-regexp '[ \t]+$|\r' "${scope[@]}" >/dev/null 2>&1; then
		echo "error: trailing whitespace or a carriage return:"
		git grep --line-number --perl-regexp '[ \t]+$|\r' "${scope[@]}"
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

# 3. HEAD's commit message trailers (check_trailers above). Commits made before
#    the Assisted-by tag carry Generated-by; this only ever looks at HEAD, so
#    those are never revisited.
msg="$(git log --max-count=1 --format=%B 2>/dev/null || true)"
if [ -n "$msg" ]; then
		check_trailers HEAD "$msg" "$(git log --max-count=1 --format='%cn <%ce>')"
fi

# 3b. Privilege boundary (CLAUDE.md): code runs as the user, so no sudo,
#     pkexec, doas, run0 or su invocation in steps, scripts, nix apps, deploy
#     code or tests. Root setup is a manual step documented in docs/, which
#     this does not scan; a step that finds one undone points at that page
#     and never spells its commands, so no line of code needs an exemption;
#     a printed prompt (`$ sudo`) or a YAML `- sudo` line counts as spelling
#     one. Beyond a regex: `systemctl` without `--user`, `busctl --system`,
#     `systemd-run --uid=0`, `machinectl shell`, `nsenter`, `ssh <host> sudo`
#     split over lines, and a command name assembled at run time.
if git grep --line-number --extended-regexp \
		"([\"']|/)(sudo|pkexec|doas|run0)([\"' ]|$)|(^|[;&|(\`{]|\\b(then|do|else|exec|env|command|xargs))[[:space:]]*(sudo|pkexec|doas|run0)([[:space:]]|\\\\|$)|[$#][[:space:]]+(sudo|pkexec|doas|run0)\\b|(^[[:space:]]*-|:)[[:space:]]+(sudo|pkexec|doas|run0)\\b|\\bssh[[:space:]][^|;&]*[[:space:]](sudo|pkexec|doas|run0)[[:space:]]|\\bsu[[:space:]]+(-|root|[[:alnum:]_-]+[[:space:]]+-c)|[\"']su[\"'][ ,]+([\"'][^\"']*[\"'][ ,]+)?[\"']-c" \
		-- 'f/*.py' 'f/*.yaml' 'f/*.ts' 'scripts/*' 'nix/*' 'deploy/*' 'tests/*' 'flake.nix' \
		':!scripts/check-style.sh' ':!scripts/get_maintainer.pl'; then
		echo "error: sudo in code; root setup is a manual step documented in docs/ (CLAUDE.md, Privilege boundary)"
		status=1
fi

# 3c. Nix first (CLAUDE.md): every tool comes from Nix, so no container
#     runtime, no distro or language package install and no piped installer
#     in steps, scripts, nix apps, deploy code or tests (deploy/README.md's
#     prose included). A Nix derivation that runs npm in its own sandbox is
#     the sanctioned exception (npm, bun and their runners), so
#     deploy/nix/*/package.nix is checked without that branch. Beyond a regex: an installer fetched in one
#     step and run in the next, and a pipe through tee.
nix_first="([\"']|\\b)(podman|docker|nerdctl|buildah|lxc|distrobox|toolbox)(-compose)?[\"']?[ ,]+[\"']?(run|build|bud|pull|push|exec|compose|image|images|volume|network|create|start|stop|rm|rmi|ps|load|save|login|system|logs|kill|up|down|launch|enter)\\b|\\bsystemd-nspawn\\b|\\b(apt|apt-get|dnf|yum|zypper|pacman|apk)[\"']?([ ,]+[\"']?-[-[:alnum:]:=]*[\"']?)*[ ,]+[\"']?(install|in|add|-S[[:alnum:]]*)([^-[:alnum:]]|$)|\\bpipx?3?[\"']?([ ,]+[\"']?-[-[:alnum:]]*[\"']?)*[ ,]+[\"']?install([^-[:alnum:]]|$)|\\b(python3?|py)[\"']?[ ,]+[\"']?-m[\"']?[ ,]+[\"']?pip\\b|\\buv[\"']?[ ,]+[\"']?(add|sync|pip|tool|run)([^-[:alnum:]]|$)|\\b(uvx|easy_install)\\b|\\b(cargo|poetry|gem|snap|flatpak|conda|mamba|go)[\"']?[ ,]+[\"']?(install|add|get)([^-[:alnum:]]|$)|\\brustup[\"']?[ ,]+[\"']?(toolchain[\"']?[ ,]+[\"']?)?install\\b|\\bbrew[\"']?[ ,]+[\"']?install([^-[:alnum:]]|$)|\\bnix-env[\"']?[ ,]+[\"']?-i|\\bnix[\"']?[ ,]+[\"']?profile[\"']?[ ,]+[\"']?install\\b|(curl|wget)[^|]*(\\|[^|]*)?\\|[[:space:]]*(sudo[[:space:]]+|env[[:space:]]+|/[^[:space:]]*/)?(ba|z|da)?sh\\b|(curl|wget)[^|]*\\|[[:space:]]*(sudo[[:space:]]+)?python3?\\b|\\b(ba|z|da)?sh[[:space:]]+(-c[[:space:]]+)?[\"']?[<$]\\(?[[:space:]]*(curl|wget)\\b"
nix_first_npm="\\b(npm|pnpm|yarn|bun)[\"']?[ ,]+[\"']?(install|i|ci|add)([^-[:alnum:]]|$)|\\b(npx|bunx)\\b|\\byarn[\"']?$"
if git grep --line-number --extended-regexp "$nix_first|$nix_first_npm" \
		-- 'f/*.py' 'f/*.yaml' 'f/*.ts' 'scripts/*' 'nix/*' 'deploy/*' 'tests/*' 'flake.nix' \
		':!scripts/check-style.sh' ':!scripts/get_maintainer.pl' ':!deploy/nix/*/package.nix' \
	|| git grep --line-number --extended-regexp "$nix_first" -- 'deploy/nix/*/package.nix'; then
		echo "error: non-Nix tooling in code; everything comes from Nix (CLAUDE.md, Nix first)"
		status=1
fi

# 3d. systemd first (CLAUDE.md): supervision is systemd's, so no detached,
#     forked, backgrounded or self-daemonizing process and no cron or at in
#     steps, scripts, nix apps, deploy code or tests; use a unit, a timer or
#     systemd-run. A thread flagged daemon=True is not a process and passes.
#     Beyond a regex: a subprocess.Popen handle never waited on.
if git grep --line-number --extended-regexp \
		"([\"']|\\b)(nohup|setsid|disown|crontab|fcrontab|anacron|start-stop-daemon|daemonize)[\"']?([ ,]|$)|\\bos\\.(fork|setsid|setpgrp|daemon)\\(|\\bDaemonContext\\b|\\bpreexec_fn\\b|start_new_session[[:space:]]*=|\\b(screen|tmux)[[:space:]]+(-d|new)|\\bat[[:space:]]+(now\\b|-f[[:space:]]|[0-9]{1,2}:[0-9]{2}\\b|(midnight|noon|teatime)\\b)|[^&>|[:space:]][[:space:]]+&[[:space:]]*([;)}][[:space:]]*)?$" \
		-- 'f/*.py' 'f/*.yaml' 'f/*.ts' 'scripts/*' 'nix/*' 'deploy/*' 'tests/*' 'flake.nix' \
		':!scripts/check-style.sh' ':!scripts/get_maintainer.pl'; then
		echo "error: process supervision outside systemd; use a unit, a timer or systemd-run (CLAUDE.md, systemd first)"
		status=1
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

# 5. Every source file in scope declares an SPDX license near the top: .rst on
#    line one as a comment, the rest (.py, .sh, .nix, .awk, .service, and the
#    extensionless scripts under deploy/nix/bin and scripts/) within the first
#    two lines, so a shebang may come first.
while IFS= read -r f; do
	case "$(head --lines=1 "$f")" in
	".. SPDX-License-Identifier: "*) ;;
	*)
		echo "error: $f: missing '.. SPDX-License-Identifier:' on the first line"
		status=1
		;;
	esac
done < <(git ls-files docs | grep --extended-regexp '\.rst$' || true)
while IFS= read -r f; do
	if ! head --lines=2 "$f" | grep --quiet 'SPDX-License-Identifier: '; then
		echo "error: $f: missing 'SPDX-License-Identifier:' in the first two lines"
		status=1
	fi
done < <(git ls-files "${scope[@]}" | grep --extended-regexp '\.(py|sh|nix|awk|service)$|^deploy/nix/bin/|^scripts/[^.]*$' || true)

[ "$status" -eq 0 ] && echo "style: OK"
exit "$status"
