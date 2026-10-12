<!-- SPDX-License-Identifier: copyleft-next-0.3.1 -->
# Verifying a merged .config

After you merge fragments with `scripts/kconfig/merge_config.sh -n` and run
`make olddefconfig`, every value a fragment requested *should* appear in the
final `.config`. It may not — a symbol can be silently dropped for three
reasons:

- **Unsatisfied Kconfig dependency.** The symbol's `depends on` was not met,
  so Kconfig refused it. Inline the missing prerequisite into the fragment.
- **Removed upstream.** The symbol no longer exists in this kernel version.
  Drop or update the fragment.
- **Last-wins override.** A later fragment on the command line set the same
  symbol to a different value (see the merge model in
  [design-decisions.md](design-decisions.md)).

## scripts/verify_config.sh

`verify_config.sh` replays the last-wins merge of the fragments you list and
compares the result against the final `.config`, reporting any mismatch. It
prints a summary of user `=y`/`=m` and infrastructure `=y`/`=m` totals at the
end, so a regression in fragment count is visible at a glance.

```sh
scripts/verify_config.sh ../build/.config \
    $C/core/64bit.config $C/core/modules.config $C/core/core.config \
    $C/core/systemd.config $C/core/initrd.config $C/arch/x86_64.config \
    $C/core/acpi-poweroff.config $C/fs/ext4.config \
    $C/virt/virtio-net.config $C/core/localversion.config
```

`$C` is this project's `kernel/configs/` directory. Pass the first argument as
the built `.config`, then the same fragments, in the same order, you gave to
`merge_config.sh`. Run it after every fragment change before committing.

A fragment or defconfig may name a symbol only an out-of-tree branch defines,
such as the Rust XArray ones. On a tree without it, that symbol is absent for
the second reason above, yet nothing is wrong. Give `-k` the kernel tree and
the script reports such a symbol as `SKIP` instead of a mismatch; a symbol the
tree's Kconfig does define and the `.config` dropped still fails.

## Verifying a defconfig

A defconfig under `defconfigs/` is checked the same way, with the defconfig as
its own fragment: expand it, then compare.

```sh
cp defconfigs/imageless_defconfig ../build/.config
make -C <linux> O=../build olddefconfig
scripts/verify_config.sh -k <linux> ../build/.config \
    defconfigs/imageless_defconfig
```

Run it after changing a defconfig, and after a fragment change the defconfig
mirrors.
