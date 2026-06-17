<!-- SPDX-License-Identifier: copyleft-next-0.3.1 -->
# Design decisions

This project ships modular kernel configuration fragments for building
custom Linux kernels targeting QEMU/KVM virtual machines. The central
mechanism is the kernel's own `scripts/kconfig/merge_config.sh`:
users pick a set of fragments, merge them on top of `allnoconfig`, and
run `make olddefconfig` to produce a `.config`. This document explains
why the fragments are organised the way they are and the upstream
rules that constrain them.

The fragments aim to be as composable as possible. When a value is
fixed, it is either required by Kconfig, a correctness constraint
coming from the kernel source, or a deliberate default that the user
can override by appending another fragment. Every such choice is
listed below with the upstream reference that justifies it.

## The override model

### `merge_config.sh -n`

All fragment usage assumes the kernel tree's `merge_config.sh` with
the `-n` flag. `-n` selects `allnoconfig` as the base: every symbol
starts at `n`. Fragments then turn on what the user wants. See:
`scripts/kconfig/merge_config.sh` and `scripts/kconfig/Makefile`
(`allnoconfig` target).

The alternative base is `olddefconfig` (keeps the existing `.config`),
but allnoconfig gives a reproducible starting point: the final
`.config` depends only on the fragment list, not on whatever happened
to be in the working directory.

### Last-wins

When two fragments set the same symbol, the last one on the command
line wins. This matches `merge_config.sh`'s behaviour, which is a
line-by-line merge with a warning when a later fragment replaces an
earlier value.

The `-y` flag ("make builtin have precedence over modules") changes
exactly one thing: it refuses to let a later `=m` demote an earlier
`=y`. It does not promote a lone `=m` to `=y` by itself; the `=y` has
to come from a `builtin/` fragment (or an explicit override) already
on the line. `-y` is the recommended flag whenever a `builtin/`
fragment is present: it makes the `=y` survive no matter where the
`builtin/` fragment sits relative to its feature fragment, so the
order of the two stops mattering.

The empirical behaviour (verified on kernel 7.1-rc6 with `make
olddefconfig` after merge):

| Flags | order `=m` then `=y` | order `=y` then `=m` |
| --- | --- | --- |
| `-n` | `=y` (last wins) | `=m` (last wins) |
| `-n -y` (recommended for `builtin/`) | `=y` | `=y` (no demotion) |

Without `-y`, the second column is a footgun: a `builtin/` fragment
placed before its feature fragment is silently demoted back to `=m`.
With `-y` both orders converge on `=y`, which is why layering
`builtin/` fragments should always pass `-y`.

This is the entire model. There is no Python driver, no custom merge
logic, no profile system: a plain line-by-line merge, plus `-y` to
make `builtin/` layering order-independent.

### scripts/verify_config.sh

After `make olddefconfig`, every value a user requested should appear
in the final `.config`. Symbols can be silently dropped for three
reasons: unsatisfied Kconfig dependencies, symbols removed upstream,
or an override from a later fragment. `scripts/verify_config.sh`
replays the last-wins merge and compares the resulting key/value pairs
against the final `.config`, reporting any mismatch. A `.config`
summary (user `=y`/`=m` and infrastructure `=y`/`=m` totals) is
printed at the end so regressions in fragment count are visible at a
glance.

## `=m` as the default

Every tristate symbol in a feature fragment is set to `=m`. Users who
want a particular feature built-in layer a matching `builtin/`
fragment on top. The default is the smallest kernel image and the
most modular system; the override is explicit.

Three reasons drive this choice:

**Smaller vmlinuz by default.** Built-in drivers contribute to the
base kernel image size, which matters for direct-kernel-boot
workflows where the image is loaded from disk or network on every
boot. With `=m`, only the drivers the VM actually needs are loaded,
and modules can be unloaded for tighter memory budgets.

**Faster iteration on out-of-tree drivers.** A driver compiled `=m`
can be rebuilt and reloaded without rebooting the guest. Kernel
developers testing driver changes in a VM want this loop to be as
tight as possible.

**Matches systemd's expectations.** systemd loads modules on demand
via `kmod-static-nodes.service` and udev. With modules, systemd
bootstraps device and filesystem support at the right points in the
boot sequence. Forcing every tristate to `=y` bypasses this and
reproduces the monolithic-kernel model from the 2.6-era.

## The `builtin/` subdirectory

`builtin/` mirrors the feature fragments that set tristates to `=m`,
with one `=y` override per feature. The naming is identical:
`virtio-fs.config` has a feature form that yields `VIRTIO_FS=m` and a
`builtin/virtio-fs.config` that yields `VIRTIO_FS=y`. A user who
wants the feature built-in adds the matching `builtin/` fragment to
the merge line and passes `-y` (see the last-wins section); with `-y`
the position of the `builtin/` fragment relative to its feature
fragment does not matter.

Each `builtin/` fragment is self-contained: it promotes the target
symbol and every tristate it depends on. For example,
`builtin/virtio-fs.config` sets `VIRTIO_FS=y`, `FUSE_FS=y`,
`VIRTIO=y`, and `VIRTIO_PCI=y`. Without all four, Kconfig would
either demote `VIRTIO_FS` back to `=m` or reject the override. The
rule: trace the `depends on` / `select` chain for every tristate and
promote all of them in the same fragment.

Only feature fragments that actually carry tristate `=m` values have a
`builtin/` counterpart. Fragments that are purely `=y`/`=n` bools
(for example `core.config`, `acpi-poweroff.config`, `gdb.config`) do
not.

### When to append `builtin/` fragments

Three common cases:

**Custom init without modprobe.** Projects that boot a Rust or C
init binary (no `/lib/modules` visible, no module loader) must
build in every driver the init touches before switch-root. Typical
set: `builtin/virtio-fs.config` plus `builtin/ext4.config` or
`builtin/virtio-net.config` depending on root source.

**Direct kernel boot with no initramfs.** When `root=` is on the
kernel command line and no initramfs is used, the entire root
device chain must be built-in.

**Host kernels that benefit from built-in drivers.** For instance,
KVM hosts where `KVM_INTEL=y`/`KVM_AMD=y` avoids a modprobe on boot,
or Docker hosts where `OVERLAY_FS=y` avoids loading overlay during
container startup.

## The base

Fragments are composed on top of a base: the non-swappable
scaffolding of a minimal bootable modern system. That base is
`core/64bit.config`, `core/core.config`, `core/modules.config`,
`core/systemd.config`, and an `arch/<arch>.config`, plus the small
boot fragments a real compose always carries (`core/initrd.config`,
`core/localversion.config`). A compose then adds a root filesystem
fragment and a transport fragment of its choice (the README example
uses `fs/ext4.config` + `virt/virtio-net.config`; a btrfs/no-ACPI
system swaps those out).

A feature fragment may assume the base is present, and only the
base. It must not assume any other feature fragment has been merged
first. The base provides the architecture-generic kernel core
(`BLOCK`, `NET`/`INET`, `PCI`, the virtio menu, `TTY`, the VFS/proc/
sysfs core) and the systemd layer (`CGROUPS`, namespaces, `SCSI`,
`IPV6`); everything past that a fragment must supply itself.

## Self-containment

Every fragment declares every config it needs above the base, so it
composes correctly with the base alone. If `ebpf.config` requires
`DEBUG_INFO`, `SECURITY`, and `NETFILTER_ADVANCED`, those lines live
in `ebpf.config` even though `gdb.config` and `security.config` also
set some of them. `merge_config.sh` deduplicates silently, so the
cost is zero and the benefit is that a user can pick `ebpf.config`
without knowing the project's internal fragment layout.

A fragment must never depend on a sibling *feature* fragment to
supply a symbol. An "advanced" or "extras" fragment inlines the
prerequisites of the feature it extends: `fs/xfs-advanced.config`
and `fs/xfs-debug.config` inline `XFS_FS`, `fs/nfsd-advanced.config`
inlines `NFSD`, `security/ima-full.config` inlines the
`SECURITY`/`INTEGRITY`/`IMA` stack, `net/netfilter-xtables.config`
inlines the xtables core and `NF_CONNTRACK`, `rust/rust-samples.config`
inlines `RUST`, and so on. Each composes on the base by itself; it
also composes with the fragment it extends (the duplicated lines
carry the same value, so the merge just dedups them).

The alternative (implicit prerequisites documented in prose) broke
down in practice: users enabled `ebpf.config` without `gdb.config`
and found `BPF_LSM`, `DEBUG_INFO_BTF`, and `NETFILTER_XT_MATCH_BPF`
silently absent. Self-containment makes the fragment its own source
of truth.

**The one exception: major optional subsystems.** A symbol whose
Kconfig dependency is itself a large optional subsystem the fragment
has no business choosing may stay conditional on a companion
fragment, as long as the header says so and the rest of the fragment
self-contains. `IMA_LSM_RULES` in `security/ima-full.config` needs
an LSM (`SELINUX`/`SMACK`/`APPARMOR`); an IMA fragment must not pick
an LSM for the user, so that one symbol only lands when
`security/lsm-modules.config` is also on the line. The header states
this; everything else in the fragment composes on the base alone.

Self-containment sometimes duplicates infrastructure across
`builtin/` fragments. `builtin/virtio-fs.config`,
`builtin/virtio-net.config`, and `builtin/vsock.config` all set
`VIRTIO=y` and `VIRTIO_PCI=y`. That is intentional: appending any of
them in isolation must promote the entire virtio chain.

## Granularity

A fragment must earn its place by providing something no other
fragment does. A fragment that is a strict subset of another (its
symbols are a subset, at the same values) is not a fragment, it is
duplication: fold it in.

A base-plus-extras split is allowed when each layer is independently
useful: `fs/xfs.config` (plain XFS) and `fs/xfs-advanced.config`
(testing features) are both worth picking on their own. The
constraint is that the layers must not redefine *each other's*
distinct symbols, and each must still self-contain its own
prerequisites per the rule above. A single-symbol fragment is fine
when the symbol is an independent toggle (`mem/ksm.config`,
`storage/zoned.config`); it is not fine when it merely names a
subset of a larger fragment.

Split by concern, not by count. The block fragments divide along the
real seam: `storage/block-layer.config` carries the block-layer knobs
(writeback throttling, debugfs, SED-OPAL), and the *drivers* live in
separate fragments by unit of intent: `storage/block-test-devices.config`
(the synthetic loop/ram/null_blk set that block testing pulls in as a
group), `storage/nvme.config` (NVMe host, pairing with the existing
`storage/nvme-fabrics.config`), and `storage/block-devices.config` (the
remaining SCSI/NBD/DRBD/bcache drivers). Each was peeled out only
because there is a real compose that wants it without the others;
drivers nobody composes separately stay together rather than becoming
one-line fragments in a tree with no index.

## Kconfig exceptions

Some symbols cannot follow the `=m` default because of Kconfig
rules or functional requirements. These are documented per-fragment
but also worth listing here.

**Serial console drivers (`x86_64.config`, `arm64.config`).**
`SERIAL_8250_CONSOLE` and `SERIAL_AMBA_PL011_CONSOLE` are bool
symbols that explicitly require their driver to be `=y`
(`depends on SERIAL_8250=y` etc., not just enabled). Without the
driver built-in, the kernel has no console during early boot, and
`console=ttyS0`/`console=ttyAMA0` silently fails.

**`SCSI=y` in `systemd.config`.** `BLK_DEV_BSG` is a bool and
depends on tristate `SCSI`. A bool depending on a tristate requires
the tristate to be `=y` for the bool to be selectable. systemd
reads block-SCSI generic ioctls via `BLK_DEV_BSG`, so `SCSI=y`
follows.

**`KUNIT=y` in `kunit.config`.** KUnit is tristate. With `=m`,
tests only run when the module is loaded, which happens late in
userspace and misses most of the boot sequence. `KUNIT=y` plus
`KUNIT_AUTORUN_ENABLED=y` runs the test suite at
`late_initcall()`, which is what KUnit CI workflows expect.

**`ACPI_TINY_POWER_BUTTON=y` in `acpi-poweroff.config`.** The driver
initialises at `device_initcall()` and registers a power button
handler before userspace starts. If `=m`, the module would not be
loaded yet when QEMU sends its first power button event via
`qmp system_powerdown`, and QEMU's
`acpi_pm1_evt_power_down()` would silently drop the event (PM1_EN
bit 0). `=y` is the only correct value for QMP graceful shutdown.

**`BRIDGE=y` would be required by Moby's `BRIDGE_NETFILTER`**, but
`BRIDGE_NETFILTER` is itself tristate, so both can be `=m`. The
same pattern applies to every bool-on-tristate in `moby.config`:
`CGROUP_CPUACCT` is bool without a tristate parent, so it stays
`=y`; the rest are tristate and follow the `=m` default.

## `VIRTIO` and the auto-select chain

Drivers in `drivers/virtio/Kconfig` all `select VIRTIO`, which is
itself tristate. `core.config` sets `VIRTIO_PCI=m`, so `VIRTIO` is
auto-selected to `=m`. Feature fragments that enable a virtio
consumer (`virtio-fs`, `virtio-net`, `vsock`, `9p`) therefore do
not need to set `VIRTIO` explicitly; the tristate select handles it.

The `builtin/` counterparts must set `VIRTIO=y` and `VIRTIO_PCI=y`
explicitly, because a tristate select with `=y` does not promote a
`=m` value. This is why `builtin/virtio-fs.config`,
`builtin/virtio-net.config`, `builtin/vsock.config`, and
`builtin/9p.config` all carry the `VIRTIO=y` and `VIRTIO_PCI=y`
lines.

## Subdirectory layout

Fragments live in topical subdirectories of `kernel/configs/`. The
subdirectories are a navigation aid, not a semantic constraint:
`merge_config.sh` concatenates fragments in the order given on the
command line regardless of path. Organising the files by theme
keeps the tree browsable as the fragment count grows.

The top-level table is documented in the README. Each fragment
carries a `# Help:` header describing what it enables and `# See:`
references to upstream documentation; open the fragment to read
its description. The `builtin/` subdirectory mirrors the same
layout exactly: `builtin/fs/ext4.config` is the `=y` override for
`fs/ext4.config`.

## Kconfig defaults are lost to allnoconfig

A subtle trap: a Kconfig symbol declared with `default y` ends up
at `n` after `merge_config.sh -n` unless something explicitly
sets or selects it. `allnoconfig` initialises every symbol to
`n`, and the fragment merge layers values on top; symbols the
fragments do not mention stay at `n` even if their Kconfig says
otherwise.

Consequences caught so far:

- `CONFIG_CPU_MITIGATIONS` (menuconfig, default `y`): every
  `MITIGATION_*` child is unreachable until the umbrella is set.
  `core/mitigations.config` writes it explicitly.
- `CONFIG_NETFILTER_INGRESS` (default `y`): gates `NF_FLOW_TABLE`,
  which gates `NFT_FLOW_OFFLOAD`. `net/nftables.config` writes it.
- `CONFIG_SWAP` (default `y`): gates `ZSWAP`. `mem/zram.config`
  writes it.
- `CONFIG_INFINIBAND_ADDR_TRANS` (default `y` under `INFINIBAND`):
  gates `NVME_RDMA`. `storage/nvme-fabrics.config` writes it.
- `CONFIG_MTRR` (def_bool `y` with an `EXPERT` prompt): gates
  `X86_PAT`. `arch/x86-extras.config` writes it.
- `CONFIG_IPV6` (menuconfig bool, default `y` as of 7.1, commit
  `309b905deee5`): gates the entire IPv6 stack and every IPv6
  netfilter symbol. `core/systemd.config` writes it explicitly. It
  was tristate through 7.0, so a stale `=m` silently demoted to `n`
  on 7.1+; see that fragment's note.

The general rule: if a Kconfig symbol says `default y` and a
fragment needs it, set it explicitly. Do not rely on the default
to survive the merge.

## Everything built-in: dropping `CONFIG_MODULES`

A kernel built with `CONFIG_MODULES=n` has no module state at all, so
`olddefconfig` promotes every requested tristate `=m` to `=y` rather
than dropping it (verified on 7.1-rc6). To produce such a kernel, omit
`core/modules.config` from the merge: that fragment is the only place
`CONFIG_MODULES=y` is set, and without it the allnoconfig default
keeps it `n`.

Because `MODULES=n` does the promotion globally, the `builtin/`
fragments are not required in this mode; the feature fragments alone
produce an all-`=y` kernel. Appending the matching `builtin/`
fragments is harmless and makes the `=y` intent explicit, but it is
the `MODULES=n` state, not the `builtin/` overrides, that promotes the
values. The `builtin/` fragments earn their keep in the other
direction: a `MODULES=y` kernel where only *some* features are built
in, which `MODULES=n` cannot express.

The result is a `.config` with `# CONFIG_MODULES is not set` and
zero `=m` lines. This is the right shape for custom-init boots,
initramfs-less boots with `root=` on the command line, recovery
kernels without modprobe, and single-purpose appliance images.

The README carries a concrete example. The key point is that this
mode is *composed*, not *separately authored*: the same fragment
set produces a modular kernel with `core/modules.config` and a
module-less kernel without it.

## Excluded configs

Several symbols that appear in the kernel's `kvm_guest.config` or
in similar downstream fragments are deliberately not in any
fragment. The list below records why.

**`CONFIG_DNOTIFY`.** Replaced by `INOTIFY_USER` (in
`systemd.config`) and `fanotify`. The kernel Kconfig help itself
says "there exist superior alternatives". No VM needs dnotify.

**`CONFIG_NTFS_FS`.** The old NTFS driver has been removed upstream;
`CONFIG_NTFS_FS` is now a backward compatibility wrapper that
selects `NTFS3_FS`. NTFS in VM guests is niche, and the new name is
not controversial, so users who need it add `CONFIG_NTFS3_FS`
directly.

**`CONFIG_XFS_SUPPORT_ASCII_CI`.** Deprecated (default `n` since
September 2025, removal planned September 2030). Vulnerable to
mixed case sensitivity attacks and UTF-8 corruption.

**`CONFIG_FS_STACK`.** Internal bool symbol with no help text, not
user-selectable. Selected automatically by stacked filesystems
(overlayfs, ecryptfs).

**`CONFIG_COMPILE_TEST`.** Turning this on exposes every driver
regardless of platform relevance, which defeats allnoconfig-based
composition. Also `LOCALVERSION_AUTO` depends on
`!COMPILE_TEST`, so `COMPILE_TEST=y` silently breaks the kernel
version string.

**`CONFIG_GUP_TEST`, `CONFIG_S390_GUEST`, `CONFIG_DRM_VIRTIO_GPU`,
`CONFIG_VIRTIO_INPUT`.** Either mm selftests unrelated to VM use,
architectures outside the project's scope (s390), or features for
graphical VMs (virtio-gpu, virtio-input) that are irrelevant for
the headless serial-console model.

**`CONFIG_SYSFS_DEPRECATED`, `CONFIG_X86_5LEVEL`,
`CONFIG_UEVENT_HELPER_PATH`, `CONFIG_BPFILTER`.** Removed from the
kernel entirely. Keeping them in any fragment would make it fail
against a modern tree.

**`CONFIG_BLK_DEV_WRITE_MOUNTED`.** Default `y` in Kconfig; setting
it again in a fragment is noise.

## `kvm_guest.config` is not referenced

The kernel tree ships `kernel/configs/kvm_guest.config` as a
convenience for running Linux in KVM guests. It sets everything to
`=y`, which conflicts with `=m` as the default. Rather than
depending on it and then overriding half its settings, the configs
it contains (`BLOCK`, `PCI`, `INET`, `VIRTIO_*`, `VIRTIO_PCI`,
`VIRTIO_BLK`, `VIRTIO_NET`, etc.) are absorbed into `core.config`,
`systemd.config`, `virtio-net.config`, and `x86_64.config`. This
gives full control over `=y` vs `=m` and eliminates an external
dependency.

The cost is a larger surface area to maintain when upstream changes
`kvm_guest.config`. The benefit is that every value is traceable to
a fragment this project owns.

## Naming and comment style

Fragment filenames are lowercase with hyphen separators
(`virtio-fs.config`, `modules-debug.config`,
`xarray-no-multi.config`). The exception is filenames that mirror an
upstream kernel arch or symbol name, which keep the upstream
underscores: `x86_64.config` (the canonical arch name),
`arm64_4k_pages.config`, `arm64_16k_pages.config`, and
`arm64_64k_pages.config` (after the `CONFIG_ARM64_*_PAGES` symbols).

Every fragment starts with a `# Help: <one-line summary>` comment,
matching the style used by the kernel tree's own
`kernel/configs/*.config` files (`kvm_guest.config`,
`hardening.config`, `tiny.config`). Longer prose follows if needed,
preceded by a blank comment line (`#`). References use `# See:` and
point at kernel documentation, an upstream source file, or (rarely)
a project README on the web.

Section headers inside a fragment use the `#\n# Section\n#` block
form also borrowed from the kernel tree. Em dashes are not used;
project style is plain ASCII punctuation only.
