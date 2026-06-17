# QEMU build flow (custom, reproducible)

Design for `f/qemu/build` — a Windmill flow that builds a custom QEMU from
source, reproducibly, for the VM layer (qsu) to consume. It mirrors the existing
`f/kernel/build` flow: a `/mirror`-backed git worktree built inside the
`nixos-flake` devShell, producing a `result.json` manifest a downstream flow
reads. The kernel flow is the template; this document is mostly "do what kernel
does, for QEMU."

Related: `qsu-execution-model.md` (how the host runs the built VM),
`nix-build-flow.md` (the guest closure), `flow-reference.md`
(OpenFlow model).

## Why build QEMU at all

The VM layer needs a `qemu-system-<arch>` binary. There are three providers, in
increasing order of reproducibility:

| Provider | Path | Reproducible? | Use |
|---|---|---|---|
| host distro | `/usr/bin/qemu-system-x86_64` | no | **never** — we skip whatever the host ships |
| custom build (**this flow**) | `WORKERS_DIR/<slot>/qemu/destdir/bin/…` | yes, given a pinned ref + the pinned nix toolchain | a specific upstream ref / patch / fork |
| nix package (future) | `/nix/store/…/bin/…` | fully hermetic | the reproducible default once wired |

The whole point is to **never depend on the host distro's QEMU**. This flow is
the custom-version path; a future variant (below) builds QEMU as a nix
derivation into the store. Both emit the same manifest, so qsu does not care
which produced the binary.

## How kdevops builds QEMU (the role we mirror)

`playbooks/qemu.yml` → `roles/qemu` is a var-driven, tag-gated pipeline on the
controller:

1. assert `git meson ninja cc` are present (else `make qemu-controller-setup`);
2. `install-deps/{debian,fedora,redhat,suse}` — distro packages;
3. `git clone {{ qemu_git }} @ {{ qemu_version }}` into `data/qemu` (`update: false`);
4. out-of-tree configure in `data/qemu-build`: `meson subprojects download`, then
   `{src}/configure --target-list={{ qemu_target }} --prefix={{ qemu_install_dir }} --disable-download`;
5. `make -j$(nproc)` (drives ninja);
6. `make install` (optionally sudo) into the destdir (`data/qemu-destdir`).

Layout is **source / qemu-build / qemu-destdir** — the same source vs.
out-of-tree-build vs. destdir split the kernel role uses. `Kconfig.mirror`
already supports a QEMU mirror, parallel to the kernel mirror.

Under kdevops-ng two stages collapse: steps 1–2 (toolchain check and distro
`install-deps`) disappear entirely, because the build runs inside
`nix develop .#build`, which already carries QEMU's full build toolchain
(`inputsFrom = [ pkgs.qemu ]` — verified: meson, ninja, GCC, pkg-config, glib,
pixman, …). No distro packages, no `qemu-controller-setup`.

## The provisioning method we copy from the kernel flow

The kernel build is backed by a shared bare mirror and per-worker worktrees
(commits `ce0a834`, `36a7a36`, `b1e0a08`, `849c471`). The chain:

1. **Host bare mirror** `/mirror/linux.git`. Each worker container mounts
   `/mirror:ro` (`windmill-worker.container.tmpl:25`). A QEMU mirror
   `/mirror/qemu.git` rides the *same* mount — no quadlet change needed.
2. **Workspace bootstrap** (idempotent): the `f/workspace/init` flow (over
   `f/workspace/fetch`) runs
   `git clone --no-checkout --reference-if-able "$MIRROR" "$UPSTREAM" workers/shared/kernel/linux`,
   where `$UPSTREAM` is the mirror's own `remote.origin.url`. The clone tracks the
   real upstream (so `fetch` pulls new commits) but borrows objects from the local
   mirror through an alternate, so it is tiny and every worktree off it is cheap.
   (The host `setup-workspace.sh` no longer clones mirrors — `init` owns that; it
   only provisions the host-sourced nixos-flake + config fragments for now.)
3. **Per-slot worktree** (`f/kernel/prepare_worktree.py`): off the shared clone,
   `git worktree add --force --detach <slot>/linux <ref>`, plus a sibling
   `<slot>/build`. `shared=false` reuses this worker's own tree
   (`WORKERS_DIR/<WORKER_INDEX>/kernel`) for every ref, so isolated builds on
   different workers run in parallel without contending; `shared=true`
   gives a shared, persistent named tree `WORKERS_DIR/shared/ws/kernel/<name>`
   (named by a `workspace` string, else a `b4_series` slug, else the job id) that
   any worker can pick up. All of it lives under `WORKERS_DIR`, bind-mounted at
   **identical host paths**, so a host-forked process (the qsu QEMU) reads the
   artifacts directly.

QEMU copies this verbatim, substituting the repo and slot names.

## The `f/qemu/build` flow

A `same_worker` pipeline, structurally `f/kernel/build` without the
config-method branch (QEMU has one configure path):

```
prepare_worktree → configure → compile → devtools → install → collect
```

| Step (`f/qemu/*.py`) | Action | Runs in |
|---|---|---|
| `prepare_worktree` | resolve a slot; detached worktree of QEMU at `ref` off the shared clone; make `build/` and `destdir/` (host `git`) | host |
| `configure` | `meson subprojects download` (in source), then `{src}/configure --target-list --prefix={destdir} --cc/--cxx --disable-download {configure_args}` in `build/` | `.#build` |
| `compile` | `make -j$(nproc)` in `build/` (drives ninja) | `.#build` |
| `devtools` | copy meson's `compile_commands.json` into the source root for clangd (on by default) | host |
| `install` | `make install` in `build/` → `destdir/` (user-writable, no sudo) | `.#build` |
| `collect` | write `result.json` and return it as the flow result | host |

Per-slot layout (worker scope): `WORKERS_DIR/<WORKER_INDEX>/qemu/{qemu,build,destdir}`.
`--prefix={destdir}` makes `make install` populate `destdir/bin` and
`destdir/share/qemu`; QEMU resolves its data dir relative to that prefix, which
is stable because the slot path is stable.

### Schema inputs (the kdevops vars, as a Windmill form)

- `qemu_ref` — tag / branch / SHA to check out from the shared clone (default
  `v11.0.0`; configurable, like the kernel flow's `git_ref`).
- `target_list` — a multiselect of QEMU's emulator targets (`--target-list`,
  enumerated from the source's `configs/targets/*.mak` — `*-softmmu` for system
  emulation, `*-linux-user`/`*-bsd-user` for user mode), default
  `[x86_64-softmmu]`, comma-joined into one argv element. Same array-select shape
  as the kernel flow's `fragments`.
- `compiler` — `gcc` (default) or `clang`, pinned via QEMU's own `--cc`/`--cxx`
  (see the toolchain note — env `CC` does **not** work here).
- `ccache` (default on) / `ccache_max_size` (default 10 GiB) — compile through
  ccache the documented QEMU way (`--cc="ccache <cc>"`, word-split into the meson
  compiler array), with the shared `write_ccache_conf` helper (`f/common/devshell`)
  the kernel's `build_flags` also uses and the devShell's `CCACHE_CONFIGPATH`
  pointing at the one config.
- `compile_commands` (default on) — copy meson's auto-generated
  `compile_commands.json` into the source root so clangd indexes the out-of-tree
  build (the `devtools` step).
- `configure_args` — free-form extra `--enable-*/--disable-*`.
- `shared` — `false` (default, this worker's own tree) or `true` (a shared named
  tree), same semantics as the kernel flow.

The source URL is **not** a flow input: it is fixed by the mirror, exactly as
the kernel flow takes a ref but not a URL. Build parallelism is `make -j$(nproc)`,
governed by the container cgroup so concurrent builds self-balance.

**Toolchain note.** The `nixos-flake#build` devShell tracks nixpkgs (26.05:
gcc-15, clang-20, curl-8.20), which runs *ahead* of QEMU releases. QEMU builds
with `-Werror`, so an older `qemu_ref` can fail on a new-library/compiler warning
— e.g. v9.2.0's `block/curl.c` passes `int` where curl 8.20 wants `long`, which
is fatal under `-Werror` (fixed in later QEMU). Prefer a recent `qemu_ref`; for
an older one, pass `configure_args: "--disable-werror"` (or `--disable-curl`).
Validated end to end: v11.0.0 and v9.2.0 (the latter with `--disable-werror`)
build a runnable `qemu-system-x86_64`.

**Compiler selection.** The devShell exports `CC=clang` (clang from
`tc.matrixExtras` wins the cc-wrapper's `CC` slot over GCC), and that overrides
any `CC` set in the environment — so the compiler must be passed through QEMU's
own `--cc`/`--cxx`, which `configure` applies during argument parsing and so
beats the devShell. (The kernel flow is immune: it passes `CC=` as a *make
variable*.) GCC, the default, builds clean. clang additionally gets
`-Qunused-arguments` (via `--extra-cflags`/`--extra-ldflags`) to silence the
spurious `-Wunused-command-line-argument` it emits on link steps for the
devShell's GCC-oriented `-Wa,--compress-debug-sections` — verified: zero such
warnings in a clang build.

### The output contract (`result.json`)

`collect` writes a manifest that becomes the flow result, parallel to the
kernel flow's manifest (`849c471`):

```json
{
  "qemu_binary": "WORKERS_DIR/<slot>/qemu/destdir/bin/qemu-system-x86_64",
  "version": "<configure-reported version>",
  "target_list": "x86_64-softmmu",
  "commit": "<resolved sha>",
  "ref": "<qemu_ref>",
  "destdir": "WORKERS_DIR/<slot>/qemu/destdir"
}
```

This is the **provider-agnostic contract**: anything that produces a
`qemu_binary` path (this flow, or the future nix-derivation variant) satisfies
it, so qsu consumes the manifest without knowing how QEMU was built.

## What to reuse vs. add

- **Reuse verbatim**: `f/common/devshell` (`DevShell` already targets
  `nixos-flake#build`; `Git` runs host-side worktree ops) and
  `f/common/worktree` (the shared slot/worktree logic). No new wiring.
- **Thin wrapper**: `f/qemu/prepare_worktree.py` wraps `f/common/worktree.prepare`
  with QEMU paths (`shared/qemu/qemu`, slot `qemu`, worktree `qemu`, plus
  `destdir` and the `VERSION` read) — the exact same step name and shape as
  `f/kernel/prepare_worktree.py`. Slot resolution, `safe.directory` handling,
  prune / reuse-or-re-add, the `shared` mode (per-worker vs shared named tree), and
  `b4 shazam` all live in the shared library.
- **Bootstrap**: the QEMU mirror is provisioned by `f/workspace/fetch`
  (a Python step over a source list, default kernel + qemu, the kernel clone also
  carrying the linux-next/stable/modules remotes), run via the `f/workspace/init`
  flow.
- **New**: `f/qemu/{configure,compile,install,collect}.py` and
  `f/qemu/build.flow`.

Prerequisite: `/mirror/qemu.git` exists on the host (a bare mirror of
`qemu/qemu.git`), parallel to `/mirror/linux.git`.

## How qsu consumes this

qsu (`~/src/qemu-system-units`, to be vendored into this repo as a **git
subtree**, like `nixos-flake` / `linux-config-fragments`) renders
`qemu-system@<vm>.service` + `virtiofsd@.service` into the user systemd manager.
A rendered unit consumes **both** build flows:

- `qemu_binary` from `f/qemu/build` → the unit's `ExecStart=` emulator;
- `bzImage` + modules from `f/kernel/build` → `-kernel` and the virtiofs
  `/lib/modules` share.

Because both manifests' paths live under `WORKERS_DIR` (bind-mounted at the same
absolute path host↔container), the host-forked unit resolves them directly — the
same property `qsu-execution-model.md` relies on. The host distro QEMU is never
referenced.

A future `f/qsu/render` step takes the two manifests plus the
`f/nix/build` closure and emits the units; that is out of scope here.

## Future variant — QEMU as a nix derivation

The reproducible end state is a `qemu_binary` that is a `/nix/store` path, not a
`WORKERS_DIR` destdir. Two ways to get there, both deferred:

- consume nixpkgs `pkgs.qemu` directly (already in the flake) — the default when
  no custom version is needed;
- build a *specific* ref as a nix derivation (`pkgs.qemu.overrideAttrs` with
  `src = <ref>`) — custom version **and** hermetic store path.

Either slots in as a second method inside `f/qemu/build` via a `branchone`,
mirroring how `f/kernel/build` offers preset (default) / make / fragments config methods.
It emits the same `result.json` (`qemu_binary` = store path), so qsu is
unchanged. We ship the meson-to-destdir method first (this document) and add the
derivation method later.

## Implementation orchestration

1. `wmill sync pull` — reconcile with the live workspace before adding anything.
2. `wmill flow new f/qemu/build --summary "QEMU build (custom, meson)"` then fill
   `flow.yaml` (modules + schema).
3. Write each step with the `write-script-python3` skill; import
   `f/common/devshell`.
4. Extend `f/workspace/fetch` (run via `f/workspace/init`) for the QEMU mirror.
5. `wmill flow preview f/qemu/build -d '{…}'` against a small `target_list` to
   validate end to end (don't deploy).
6. `wmill sync push` to deploy (Option B — no CI runs `wmill sync push` in this
   repo).
