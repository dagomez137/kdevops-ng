#!/usr/bin/env bash
# SPDX-License-Identifier: copyleft-next-0.3.1
# Run a vendored project's own verification steps, as its docs/verifying.md
# (or README, for linux-config-fragments) lists them, from the checkout top:
#   nix develop .#vendor --command bash scripts/check-vendor.sh <project> [file...]
# The files are the ones a commit touched; linux-config-fragments merges the
# touched fragments onto its minimal VM set, then expands and verifies every
# defconfig. Every command is printed before
# it runs. The vendor shell provides KDEVOPS_KERNEL_SRC and
# KDEVOPS_SYSTEMD_UNITS.
set -o errexit -o nounset -o pipefail

run() {
	printf '$ %s\n' "$*"
	"$@"
}

top="$(git rev-parse --show-toplevel)"
out="$(mktemp --directory)"
trap 'rm --recursive --force "$out"' EXIT

nixos_flake() {
	local d="$top/vendor/nixos-flake"
	mapfile -t nixfiles < <(git -C "$d" ls-files '*.nix')
	run nixfmt --check "${nixfiles[@]/#/$d/}"
	run nix flake check "git+file://$top?dir=vendor/nixos-flake"
	run nix build --no-link "path:$d/templates/imageless#packages.x86_64-linux.toplevel" \
		--override-input nixos-flake "path:$d"
	run nix build --no-link "path:$d/templates/libvirt#packages.x86_64-linux.image" \
		--override-input nixos-flake "path:$d"
	mapfile -t pkgattrs < <(nix eval --raw \
		"git+file://$top?dir=vendor/nixos-flake#packages.x86_64-linux" --apply \
		'ps: builtins.concatStringsSep "\n" (builtins.attrNames ps)')
	run nix build --no-link "${pkgattrs[@]/#/$d#}"
}

qemu_system_units() {
	local d="$top/vendor/qemu-system-units" t name
	for t in "$d"/templates/*.j2; do
		name="$(basename "$t" .j2)"
		run minijinja-cli --trim-blocks --output "$out/$name" \
			--define share_tag=modules \
			--define qemu_binary="$(command -v qemu-system-x86_64)" \
			--define virtiofsd_binary="$(command -v virtiofsd)" \
			"$t" "$d/vars/example.yaml"
	done
	run env SYSTEMD_UNIT_PATH="$out:$KDEVOPS_SYSTEMD_UNITS/user" systemd-analyze verify \
		--user --man=no "$out/qemu-system@.service" \
		"$out/virtiofsd@.socket" "$out/virtiofsd@.service"
	# A review aid, not a gate: the units ship unhardened by design, and the
	# default threshold never fails.
	run env SYSTEMD_UNIT_PATH="$out:$KDEVOPS_SYSTEMD_UNITS/user" systemd-analyze security \
		--user --offline=true "$out/qemu-system@.service"
	run minijinja-cli --trim-blocks \
		--define 'pci_passthrough:=[{"addr": "0000:2d:00.0"}]' \
		--output "$out/10-vfio-kvm.rules" \
		"$d/templates/vfio-udev.rules.j2" "$d/vars/example.yaml"
	run udevadm verify "$out/10-vfio-kvm.rules"
	run bash -n "$out/transient-run.sh"
}

linux_config_fragments() {
	local d="$top/vendor/linux-config-fragments" c f
	c="$d/kernel/configs"
	local frags=(
		"$c/core/64bit.config" "$c/core/modules.config" "$c/core/core.config"
		"$c/core/systemd.config" "$c/core/initrd.config" "$c/arch/x86_64.config"
		"$c/core/acpi-poweroff.config" "$c/fs/ext4.config"
		"$c/virt/virtio-net.config" "$c/core/localversion.config"
	)
	local builtin=()
	for f in "$@"; do
		[ -e "$top/$f" ] || continue
		case "$f" in
		*/kernel/configs/arch/arm64*) ;;
		*/kernel/configs/builtin/*) builtin+=("$top/$f") ;;
		*/kernel/configs/*.config) frags+=("$top/$f") ;;
		esac
	done
	local yes=()
	[ "${#builtin[@]}" -eq 0 ] || yes=(-y)
	mkdir "$out/merge"
	touch "$out/merge/.config"
	(run cd "$out/merge" && run sh "$KDEVOPS_KERNEL_SRC/scripts/kconfig/merge_config.sh" \
		"${yes[@]}" -m -O "$out/merge" "$out/merge/.config" "${frags[@]}" "${builtin[@]}")
	run make --silent -C "$KDEVOPS_KERNEL_SRC" O="$out/merge" \
		KCONFIG_ALLCONFIG="$out/merge/.config" alldefconfig
	run sh "$d/scripts/verify_config.sh" -k "$KDEVOPS_KERNEL_SRC" \
		"$out/merge/.config" "${frags[@]}" "${builtin[@]}"
	for f in "$d"/defconfigs/*; do
		rm --recursive --force "$out/def"
		mkdir "$out/def"
		run cp "$f" "$out/def/.config"
		run make --silent -C "$KDEVOPS_KERNEL_SRC" O="$out/def" olddefconfig
		run sh "$d/scripts/verify_config.sh" -k "$KDEVOPS_KERNEL_SRC" "$out/def/.config" "$f"
	done
}

project="${1:?usage: check-vendor.sh <project> [file...]}"
shift
case "$project" in
nixos-flake) nixos_flake ;;
qemu-system-units) qemu_system_units ;;
linux-config-fragments) linux_config_fragments "$@" ;;
*)
	echo "check-vendor: unknown project $project" >&2
	exit 2
	;;
esac
