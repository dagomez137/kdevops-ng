# Nix build flow

How `f/nix/build` builds a NixOS system with Nix. Today it builds the
**imageless** product — a `toplevel` system whose *closure* a host-forked QEMU
boots over virtiofs with an external kernel; other products (e.g. a `libvirt`
disk-image system) could be added under the same flow later. The model is the
kdevops `nixosfi` role reimplemented as a Windmill flow.

Status: the build flow (`render → lock → build`) is **implemented**; booting the
closure is the qsu half (parked, see
[qsu-execution-model.md](qsu-execution-model.md)).

## The one distinction everything follows from

The vendored `vendor/nixos-flake` is consumed in **two unrelated ways**,
and the right answer to "should we generate our own flake?" is opposite for each.

| | Kernel build (today) | NixOS closure (this doc) |
|---|---|---|
| Flake output used | `devShells.x86_64-linux.build` | `nixosConfigurations.vm…toplevel` |
| Nix's role | **provide a toolchain** (GCC/make/bison…) | **build the artifact** (a NixOS system) |
| How invoked | `nix develop .#build --command make …` | `nix build path:<dir>#toplevel` |
| Is the artifact a derivation? | **No** — `make` drives the build inside the shell | **Yes** — the closure *is* the derivation |
| Need a generated flake? | **No** | **Yes** (a per-VM config flake) |
| Lives in | `f/kernel/` (unchanged) | `f/nix/` (new) |

A kernel build is deliberately *not* a Nix derivation: the flake's own comment is
*"Nix's role is to PROVIDE the toolchain; the pipeline decides which
compiler/flags/targets to use inside it."* Generating a flake to compile a kernel
would be a category error. **`f/kernel` stays on `#build`.**

Note: the kernel build does **not** use `modules/profiles/controller.nix`. That is
a NixOS-*host* module (`environment.systemPackages` + `libvirtd`); we are podman
containers, not a NixOS host, so we never evaluate it. The `#build` devShell and
`controller.nix` only share the same package list via `lib/toolchain.nix` — we get
the controller's kernel toolchain as an ephemeral shell, without the NixOS-host
baggage.

## Why `f/nix` (not `f/nixos`, not `f/nixosfi`)

Nix is the umbrella; NixOS is one thing you build with it, and every operation here
goes through the `nix` CLI. The existing `f/nix/hello` is `nix run nixpkgs#hello` —
generic Nix the tool, nothing NixOS about it; `f/nixos` would mis-home it. A single
`f/nix` bucket cleanly holds both "run a package" and "build a NixOS system" — all
Nix operations. Booting the closure is *not* a Nix operation (host-systemd VM
lifecycle), so qsu stays a separate future bucket `f/qsu/`. This mirrors kdevops's
own split: the `nixosfi` role builds the closure, the `qsu` role boots it.

## Equivalent to kdevops nixosfi — not identical

The **Nix-facing contract is identical**; the **orchestration is equivalent but
reimplemented**.

| Layer | kdevops `nixosfi` role | This flow | Same? |
|---|---|---|---|
| Library | `scripts/nixos-flake` (vendored) | `vendor/nixos-flake` (vendored) | identical (same repo) |
| Per-VM artifact | dir: `flake.nix`+`default.nix`+`flake.lock` | same | identical shape |
| `flake.nix` | `flake.nix.j2`, mirrors `templates/imageless/flake.nix` | render from that same template | identical output |
| Modules | `nixosModules.{backends.imageless,user,profiles.*,testSuites.*,mounts.*}` | same | identical |
| Build | `nix build path:<dir>#toplevel --out-link result` | same | identical |
| Boot artifact | read `result/boot.json` → init/initrd | same | identical |
| *Which* modules | Kconfig `output yaml` → Ansible vars → Jinja2 `{% if %}` | Windmill flow inputs (JSON schema) → step code | equivalent, different machinery |
| Templating | Jinja2 (`template:`) | plain Python string builder | different engine, same output |
| Orchestration | Ansible role, tag-gated phases | Windmill flow, step modules | equivalent, reimplemented |
| VM runtime | separate `qsu` role | separate `f/qsu/*` (parked) | equivalent (same execution model) |

kdevops is itself *not* identical to the canonical upstream flow: upstream says
`nix flake init --template`, but kdevops chose to **mirror** the templates as Jinja2
and render them (`flake.nix.j2`: *"Mirrors upstream's starter at
scripts/nixos-flake/templates/imageless/flake.nix"*). We make the same choice —
render from our vendored `templates/imageless/`, the same source kdevops mirrors —
so the closure we build is byte-for-byte what kdevops builds. Only the *driver*
differs.

Rejected alternatives:
- **Wrap kdevops** (`ansible-playbook nixosfi.yml`): literally identical, but drags
  in the whole kdevops tree + Ansible + Kconfig + inventory — against the
  granular-native-steps direction.
- **Diverge** (invent our own closure shape): loses the "same closure as kdevops"
  guarantee for no benefit; our qsu model already *is* the kdevops imageless model.

## The per-VM config contract (idiomatic Nix)

The unit of customization is a **per-VM configuration directory** with its own
`flake.nix` + `default.nix` + `flake.lock` (`nixos-flake/docs/usage.md` "Multiple
configurations"). Not one global flake rebuilt in place.

- `inputs.nixos-flake.url = "path:<abs>/vendor/nixos-flake"` — we already
  vendor it there.
- `inputs.nixpkgs.follows = "nixos-flake/nixpkgs"` — avoid a second nixpkgs.
- `flake.nix` is essentially **static**; `nixos-flake` + `inputs` are passed via
  `specialArgs`, so all per-VM composition lives in **`default.nix`** (imports,
  overlays, hostname, SSH keys). kdevops's `flake.nix.j2` varies only by the
  optional `<pkg>-src` override inputs.
- The imageless kernel is **external** (`boot.kernel.enable = false`), so the
  kernel image + `/lib/modules` come from `f/kernel/build`, not from the closure.
  The kernel must have `CONFIG_VIRTIO_FS=y`, `CONFIG_VIRTIO_PCI=y`,
  `CONFIG_TMPFS=y` built-in — exactly what our `imageless_defconfig` preset
  guarantees. Clean closure: **preset config → kernel → imageless boot.**

### Footguns to honor

- **`path:` scheme, not git.** We build with `nix build path:<dir>#toplevel` (as
  kdevops does). The `path:` fetcher copies the whole config dir into the store, so
  the generated files do **not** need to be git-tracked — the "flakes only see
  git-tracked files" rule in `nixos-flake/docs/usage.md` applies only to bare /
  `git+file` flakerefs. No `git init` in the flow.
- **`path:` does not expand `~`.** Use absolute `WORKERS_DIR` paths (we already do).
- **Keep `flake.lock`** per config for reproducibility (the `lock_config` step).
  Re-pinning the vendored nixos-flake is `nix flake update --flake path:<dir>
  nixos-flake`.
- **Bootspec, not `$out/initrd`.** Because `boot.kernel.enable = false`, the closure
  has no `$out/kernel`/`$out/initrd` symlinks; `init`/`initrd` come from the standard
  NixOS bootspec (RFC-0125) at `<toplevel>/boot.json` (`org.nixos.bootspec.v1`).

## Flow design

```
f/nix/
  build.flow/              # NixOS build — render → lock → build (imageless today)
    flow.yaml
  render_config.py
  lock_config.py
  build_closure.py
f/common/
  devshell.py              # MOVED from f/kernel/devshell.py (now shared)
  devshell.script.yaml     #   adds a Nix runner (raw `nix` CLI) beside DevShell/Git
```

`value.same_worker: true` so the per-VM config dir created by step 1 is visible to
steps 2–3 on the same worker slot.

### Step 1 — `render_config.py`

Inputs (typed `main(...)`):

| param | type | default | meaning |
|---|---|---|---|
| `vm_name` | `str` | — | guest hostname / config dir name |
| `profiles` | `list[str]` | `["devel","build-tools","monitoring"]` | `nixosModules.profiles.*` to import |
| `test_suites` | `list[str]` | all 8 | `nixosModules.testSuites.*` to import |
| `shares` | `dict` | `{}` | virtiofs shares → `nixos-flake.shares` |
| `overrides` | `list[dict]` | `[]` | per-package `src` overrides (pkg/src/ref) |
| `ssh_keys` | `list[str]` | `[]` | authorized keys for root + user |
| `user_name` | `str` | `"kdevops"` | unprivileged account (`nixos-flake.user.name`) |

**Featured by default.** With no `profiles`/`test_suites` passed, the closure is
fully featured: the three guest profiles (`devel`, `build-tools`, `monitoring`) and
all eight test suites. `devel`/`build-tools` are active on import; `monitoring` is
gated, so render emits `nixos-flake.monitoring.enable = true` whenever it (or any
gated profile) is selected. `controller` is a host role — it pulls libvirtd into the
guest and upstream only composes it on the libvirt backend — so it is an available
option but **off** by default. Pare the lists back per run for a lighter closure.

This flow takes no kernel input. The closure sets `boot.kernel.enable = false` and
its initrd loads no modules, so it is built entirely independently of the kernel;
pairing the two is a qsu concern (see *Composition* below).

- Writes `<slot>/nix/<vm>/flake.nix` — near-verbatim copy of
  `templates/imageless/flake.nix`, only `nixos-flake.url` set to the vendored
  absolute path (+ one `<pkg>-src` input per override).
- Writes `<slot>/nix/<vm>/default.nix` — **generated in plain Python** from the
  typed inputs (no Jinja2 dependency). Mapping sketch:

  ```python
  # The flake's modules list already imports the imageless backend + user + overlay,
  # so default.nix only ADDS the per-VM composition.
  imports  = [f"nixos-flake.nixosModules.profiles.{p}"   for p in profiles]
  imports += [f"nixos-flake.nixosModules.testSuites.{t}" for t in test_suites]
  if shares: imports.append("nixos-flake.nixosModules.mounts.shares")
  # → emit:  { imports = [ … ]; networking.hostName = "<vm>";
  #            nixos-flake.user.name = "<user>";
  #            users.users.<root|user>.openssh.authorizedKeys.keys = [ … ];
  #            nixos-flake.shares."<mnt>" = { tag = …; };
  #            nixpkgs.overlays = lib.mkAfter [ … src overrides … ]; }
  ```

- **Prints both rendered files** before returning (same debuggability discipline as
  the printed commands). Path-traversal hardening on `vm_name`, like
  `configure_preset` (`.resolve()` + ancestor check); profiles/testSuites validated
  against the known module sets.
- Returns `{config_dir, flake, default, nixos_flake, vm_name}`.

### Step 2 — `lock_config.py`

- `nix flake lock path:<dir>` — materialise `flake.lock` (pins the vendored
  nixos-flake input; nixpkgs follows it). No git needed (`path:` scheme).
- `update: bool` re-pins nixos-flake (`nix flake update --flake path:<dir>
  nixos-flake`).
- Returns `{config_dir, lock}`.

### Step 3 — `build_closure.py`

- `nix build path:<dir>#toplevel --out-link <dir>/result --print-out-paths`.
- Read `<dir>/result/boot.json` (`org.nixos.bootspec.v1`) → return
  `{config_dir, toplevel, init, initrd, boot_json}`.

Steps 1–3 are **fully runnable today** — `nix build` needs no host-systemd — so the
entire flake-generation half is provable before qsu exists.

### Parked → `f/qsu/`

Render systemd units (QEMU + two virtiofsd: `store` → host `/nix/store`,
`modules` → the build's `/lib/modules`) and start the machine, per
[qsu-execution-model.md](qsu-execution-model.md). `systemd-run --user` from the
worker forks on the **host**, so units must reference only `/nix/store` +
`WORKERS_DIR` paths.

## Composition with the kernel build flow

The closure build and the kernel build are independent; they meet only at qsu boot:

```
f/kernel/build  →  { bzImage, destdir (/lib/modules) } ──── external kernel ────┐
                                                                                │
f/nix/build(vm_name, profiles, …)  →  { toplevel, init, initrd }                │
                        │                                                       │
                        └──────────────►  f/qsu/* (parked)  ◄───────────────────┘
                                          boots the closure with the external kernel
```

## Shared `devshell.py`

`DevShell` / `Git` / `_log` move from `f/kernel/devshell.py` to **`f/common/devshell.py`**
(a neutral home now that two domains need them), and a **`Nix`** runner is added for
the raw `nix` CLI (`build`, `flake lock`) the `f/nix/*` steps use. Both `f/kernel/*`
and `f/nix/*` import via Windmill relative imports:

```python
from f.common.devshell import DevShell, Git, Nix
```

The move updates the `from f.kernel.devshell import …` lines in the seven
`f/kernel/*` steps. Behavior is unchanged: argv lists (no shell), printed
copy-pasteable commands, `cwd=` support, nix-profile PATH. `Nix` enables flakes +
the new CLI per-invocation (`--extra-experimental-features "nix-command flakes"`) so
the worker's nix.conf need not be configured.

## References

- [`flow-reference.md`](flow-reference.md) — OpenFlow/script model.
- [`qsu-execution-model.md`](qsu-execution-model.md) — host-systemd boot model (qsu).
- `vendor/nixos-flake/README.md` — backends, controller, boot model.
- `vendor/nixos-flake/docs/usage.md` — configurations, modules, overlays,
  the git-tracked requirement, source overrides.
- `vendor/nixos-flake/templates/imageless/{flake,default}.nix` — the
  starter we render from (same source kdevops's `nixosfi` role mirrors).
- kdevops `playbooks/roles/nixosfi/` + `playbooks/roles/qsu/` — the Ansible role
  this flow reimplements.
</content>
</invoke>
