#!/bin/sh
# SPDX-License-Identifier: copyleft-next-0.3.1
#
# verify_config.sh - Verify that requested config fragment values match
# the final .config produced by merge_config.sh.
#
# Reports mismatches where a requested value was not applied (due to
# unsatisfied dependencies, removed symbols, or Kconfig overrides).
# Handles the last-wins rule: when multiple fragments set the same
# symbol (e.g., core.config sets =m and builtin/core.config sets =y),
# only the last value is verified.
#
# Prints a summary of the final .config broken down by =y and =m counts,
# separating user-configurable symbols from infrastructure (ARCH_HAS_*,
# HAVE_*, CC_*, AS_*, GENERIC_*, X86_*).
#
# With -k, a requested symbol that the kernel tree's Kconfig does not
# define (an out-of-tree driver on a mainline tree) is reported as skipped
# rather than as a mismatch.
#
# Usage:
#   verify_config.sh [-k <srctree>] <dotconfig> <fragment> [<fragment> ...]
#
# Example:
#   verify_config.sh ../build/.config \
#       kernel/configs/64bit.config \
#       kernel/configs/modules.config \
#       kernel/configs/core.config

set -e

CONFIG_PREFIX=${CONFIG_-CONFIG_}

AWK="${AWK:-awk}"

usage() {
	printf 'Usage: %s [-k <srctree>] <dotconfig> <fragment> [<fragment> ...]\n\n' "$0"
	printf 'Verify that config values requested in fragments match the\n'
	printf 'final .config. Exit code 0 if all match, 1 if any mismatch.\n'
	printf 'With -k, skip symbols the Kconfig of <srctree> does not define.\n'
	exit 1
}

SRCTREE=
if [ "$1" = "-k" ]; then
	[ "$#" -ge 2 ] || usage
	SRCTREE="$2"
	shift 2
	if [ ! -d "$SRCTREE" ]; then
		printf 'error: %s is not a directory\n' "$SRCTREE" >&2
		exit 1
	fi
fi

if [ "$#" -lt 2 ]; then
	usage
fi

DOTCONFIG="$1"
shift

if [ ! -r "$DOTCONFIG" ]; then
	printf 'error: %s does not exist or is not readable\n' "$DOTCONFIG" >&2
	exit 1
fi

for fragment in "$@"; do
	if [ ! -r "$fragment" ]; then
		printf 'error: %s does not exist or is not readable\n' "$fragment" >&2
		exit 1
	fi
done

# Build a lookup of all config values from .config in a single pass.
TMP_LOOKUP="${TMPDIR:-/tmp}/verify_config.XXXXXX"
TMP_LOOKUP=$(mktemp "$TMP_LOOKUP")

# Build the merged requested values (last-wins across all fragments).
TMP_REQUESTED="${TMPDIR:-/tmp}/verify_requested.XXXXXX"
TMP_REQUESTED=$(mktemp "$TMP_REQUESTED")

# The symbols the kernel tree defines, for -k.
TMP_DEFINED="${TMPDIR:-/tmp}/verify_defined.XXXXXX"
TMP_DEFINED=$(mktemp "$TMP_DEFINED")

trap 'rm -f "$TMP_LOOKUP" "$TMP_REQUESTED" "$TMP_DEFINED"' EXIT

if [ -n "$SRCTREE" ]; then
	# shellcheck disable=SC2016
	find "$SRCTREE" -name 'Kconfig*' -type f -exec "$AWK" -v prefix="$CONFIG_PREFIX" '
	$1 == "config" || $1 == "menuconfig" { print prefix $2 }
	' {} + | sort -u > "$TMP_DEFINED"
fi

# shellcheck disable=SC2016
"$AWK" -v prefix="$CONFIG_PREFIX" '
/^[^ #]/ && index($0, prefix) == 1 {
	print $0
	next
}
/^# / && / is not set$/ {
	sym = $2
	if (index(sym, prefix) == 1)
		print sym "=n"
}
' "$DOTCONFIG" > "$TMP_LOOKUP"

# Merge all fragments: last value wins (same as merge_config.sh).
# Output: CONFIG_FOO=value with source fragment name.
# shellcheck disable=SC2016
"$AWK" -v prefix="$CONFIG_PREFIX" '
BEGIN { n = 0 }
{
	# Handle "# CONFIG_FOO is not set" form
	if ($0 ~ /^# / && / is not set$/) {
		sym = $2
		if (index(sym, prefix) == 1) {
			val = "n"
			requested[sym] = val
			source[sym] = FILENAME
			if (!(sym in order)) { order[sym] = n++ }
		}
		next
	}
	# Skip comments and blank lines
	if ($0 ~ /^#/ || $0 ~ /^$/) next
	# Parse CONFIG_FOO=value
	if (index($0, prefix) == 1) {
		eq = index($0, "=")
		if (eq == 0) next
		sym = substr($0, 1, eq - 1)
		val = substr($0, eq + 1)
		# Validate symbol name
		if (sym !~ /^CONFIG_[A-Za-z0-9_]+$/) next
		requested[sym] = val
		source[sym] = FILENAME
		if (!(sym in order)) { order[sym] = n++ }
	}
}
END {
	for (sym in requested)
		print sym "=" requested[sym] "\t" source[sym]
}
' "$@" > "$TMP_REQUESTED"

# Strip surrounding quotes from a value.
strip_quotes() {
	_val="$1"
	case "$_val" in
		\"*\") _val="${_val#\"}"; _val="${_val%\"}" ;;
	esac
	printf '%s\n' "$_val"
}

mismatches=0
checked=0
skipped=0

while IFS="	" read -r entry src || [ -n "$entry" ]; do
	sym="${entry%%=*}"
	requested="${entry#*=}"
	src_name=$(basename "$src")

	checked=$((checked + 1))

	# Look up actual value from pre-built lookup
	actual_line=$(grep "^${sym}=" "$TMP_LOOKUP" 2>/dev/null || true)

	if [ -n "$actual_line" ]; then
		actual="${actual_line#*=}"
		if [ "$(strip_quotes "$requested")" = "$(strip_quotes "$actual")" ]; then
			continue
		fi
	else
		actual="(absent)"
		if [ "$requested" = "n" ]; then
			continue
		fi
		if [ -n "$SRCTREE" ] && ! grep -qx "$sym" "$TMP_DEFINED"; then
			printf 'SKIP %s: %s=%s, not defined by %s\n' \
				"$src_name" "$sym" "$requested" "$SRCTREE"
			skipped=$((skipped + 1))
			continue
		fi
	fi

	printf 'MISMATCH %s: %s=%s => %s\n' "$src_name" "$sym" "$requested" "$actual"
	mismatches=$((mismatches + 1))

done < "$TMP_REQUESTED"

# Count =y and =m in the final .config, split by user vs infrastructure.
# shellcheck disable=SC2016
"$AWK" -v prefix="$CONFIG_PREFIX" '
BEGIN {
	user_y = 0; user_m = 0
	infra_y = 0; infra_m = 0
}
/^[^ #]/ && index($0, prefix) == 1 {
	eq = index($0, "=")
	if (eq == 0) next
	sym = substr($0, 1, eq - 1)
	val = substr($0, eq + 1)

	infra = 0
	if (index(sym, "CONFIG_ARCH_") == 1) infra = 1
	if (index(sym, "CONFIG_HAVE_") == 1) infra = 1
	if (index(sym, "CONFIG_CC_") == 1) infra = 1
	if (index(sym, "CONFIG_AS_") == 1) infra = 1
	if (index(sym, "CONFIG_GENERIC_") == 1) infra = 1
	if (index(sym, "CONFIG_X86_") == 1) infra = 1
	if (index(sym, "CONFIG_PGTABLE_") == 1) infra = 1
	if (index(sym, "CONFIG_NEED_") == 1) infra = 1
	if (index(sym, "CONFIG_TOOLCHAIN_") == 1) infra = 1

	if (val == "y") {
		if (infra) infra_y++; else user_y++
	} else if (val == "m") {
		if (infra) infra_m++; else user_m++
	}
}
END {
	printf "\n.config summary:\n"
	printf "  user:  %4d =y  %4d =m  %4d total\n", user_y, user_m, user_y + user_m
	printf "  infra: %4d =y  %4d =m  %4d total\n", infra_y, infra_m, infra_y + infra_m
	printf "  all:   %4d =y  %4d =m  %4d total\n", user_y + infra_y, user_m + infra_m, user_y + user_m + infra_y + infra_m
}
' "$DOTCONFIG"

if [ "$mismatches" -gt 0 ]; then
	printf '\n%d mismatch(es) out of %d fragment configs checked\n' "$mismatches" "$checked"
	exit 1
else
	printf '\nOK: %d fragment configs verified, all match' "$checked"
	if [ "$skipped" -gt 0 ]; then
		printf ' (%d skipped, not in the tree)' "$skipped"
	fi
	printf '\n'
	exit 0
fi
