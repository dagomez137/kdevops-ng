# Handoff: choosing the Rust index replay mechanism

Repo: `/home/dagomez/src/kdevops-ng`, branch `main`. The design is settled in
`notes/adr/0013-rust-index-regenerated-on-the-consumer.md` and the whole
sequence in `notes/plans/kernel-rust-index.md` is **implemented and committed**.
The feature is live: a developer worktree gets both indexes, proc macros expand,
and it was validated end to end on the rxarray identity.

This note is now the record of how the mechanism was chosen and of what is
still genuinely unknown. Everything answered since is in "Answered since"
below; E4 and Q3 through Q5 remain open and each needs a peer host, a dirty
worktree, or a measurement nobody has taken.

Read the ADR first. Everything below assumes it.

## Status: the mechanism is settled

E1 was run on 2026-08-07 and route B won outright. The consumer skips the
top-level goal and enters the sub-make the goal would have entered:

    make -f <wt>/scripts/Makefile.build obj=rust rust-analyzer \
        srcroot=<wt> srctree=<wt> objtree=<build> RUSTC=rustc

Measured on a freshly materialized layer plus `rustc_cfg`, `auto.conf` and the
seven generated `*.rs`: 1.1 seconds, exit 0, exactly one file written, and the
index path-normalized identical to the builder's across all 46 crates including
the sysroot. `scripts/Makefile.build:33` soft-includes `auto.conf`, so no
kconfig runs and `.config` is never read or written.

The near-miss is the finding worth keeping. Without `auto.conf` the sub-make
still exits 0 and still writes a well-formed index, but
`scripts/Makefile.compiler:72` defines `rustc-min-version` as a test against
`CONFIG_RUSTC_VERSION`, a kconfig symbol, so `core-edition` falls back from 2024
to 2021 and the `proc_macro2` crate silently loses `proc_macro_span_file` and
`proc_macro_span_location`. Gate on the inputs being present, never on the exit
status.

E2 is therefore moot: nothing needs recording at publish time, so no carrier has
to be invented and `make V=1` never has to be parsed. E3 is moot too, since the
chosen route cannot reach kconfig and so has no `.config` to guard. Q1 and Q2
were answered later by observation and are recorded below; E4 and Q3 through Q5
are still open.

The original framing follows, for the reasoning that led here.

## The open question (settled, kept for the reasoning)

The consumer must produce `rust-project.json` for its own worktree. Two ways to
reach the kernel's generator, and the difference is not cosmetic.

**Route A, the make goal.** `make --directory=<wt> O=<build> LLVM=1
rust-analyzer`. Proven end to end: 12.5 seconds cold, 2 seconds warm, 46 crates,
zero unresolvable roots, and the resulting index is path-normalized identical to
the builder's, sysroot included. kbuild computes the generator's arguments, so
nothing has to be transported or transcribed.

The cost is that `rust-analyzer` is absent from `no-dot-config-targets`, so the
goal implies `need-config`, which implies syncconfig, which re-probes the
toolchain and writes back into the developer's `.config`. With `LLVM=1` derived
from `CONFIG_CC_IS_CLANG=y` the file was measured byte-pristine. With the flag
merely forgotten, inside the correct devShell, kconfig went interactive, emitted
22 prompts, flipped `CONFIG_CC_IS_CLANG=y` to `CONFIG_CC_IS_GCC=y`,
`CONFIG_LD_IS_LLD=y` to `CONFIG_LD_IS_BFD=y`, `CONFIG_AS_IS_LLVM=y` to
`CONFIG_AS_IS_GNU=y`, added `CONFIG_GCC_PLUGINS=y`, and still wrote a
well-formed `rust-project.json` naming `~/.rustup`. So route A is one forgotten
token away from silently corrupting the developer's config and handing them a
plausible wrong index.

**Route B, the generator directly.** `python3
<wt>/scripts/generate_rust_analyzer.py <cfgs...> <wt> <build> <sysroot>
<lib_src>`, which is the exact sibling of what `f/kernel/fetch_devel` already
does for the C index: it runs `gen_compile_commands.py`, not
`make compile_commands.json`. No kbuild, no kconfig, so the entire corruption
class is structurally impossible rather than guarded against. Under 2 seconds.

The cost is that the generator's `--cfgs`, `--envs` and `core_edition`
arguments live in `rust/Makefile:74-152` and would have to reach the consumer
somehow. **This is the thing to settle.**

## What is already known about route B's arguments

Verified by reading the sources, not assumed:

- The generator reads the objtree in exactly two places,
  `open(objtree/include/generated/rustc_cfg)` at
  `scripts/generate_rust_analyzer.py:77` and `objtree.resolve(True)` at `:303`.
  A grep of the whole 408-line script for `.config|auto.conf|autoconf|CONFIG_`
  returns zero hits.
- Every `--cfgs` and `--envs` value the recipe passes is defined **outside**
  `rust/Makefile`'s `ifdef CONFIG_RUST` guard, at lines 74 to 152, and every one
  is a static literal or a `rustc-min-version` test: `core-cfgs :=
  no_fp_fmt_parse`, `core-edition := $(if $(call rustc-min-version,108700),
  2024,2021)`, `zerocopy-envs := CARGO_PKG_VERSION=0.8.50`, `proc_macro2-cfgs`,
  `quote-cfgs`, `syn-cfgs`, `pin_init_internal-cfgs := kernel
  USE_RUSTC_FEATURES`, `pin_init-cfgs`. Not one depends on a CONFIG symbol.
  They depend on the rustc version only.
- The positional layout today is `core_edition srctree objtree sysroot
  sysroot_src [exttree]` (`rust/Makefile:646-654`,
  `scripts/generate_rust_analyzer.py:375-380`), with `exttree` fed only from
  `KBUILD_EXTMOD`, which this repo never sets.
- Merely parsing `rust/Makefile` shells out to `rustc` five times, three for
  `procmacro-name` and two for the sysroot and host target probes
  (`rust/Makefile:52-63`).

So the arguments are a pure function of the rustc version, which means the
consumer can in principle derive them itself with no transport. The question is
how to do that without transcribing upstream's literals into our Python, which
would rot against the kernel tree and break CLAUDE.md's one-true-source rule.

## Experiments to run

Work non-destructively. The reproduction recipe that worked: `git clone
--shared --no-checkout` from `workbench/system/bare/linux.git`, `checkout
--detach` at the built commit, then materialize the devel layer into a scratch
objtree with `cp --recursive --force <storepath>/. <scratch>/build/` and `chmod
--recursive u+w`. kbuild with `O=` writes only into the objtree, so the source
stays clean; this was confirmed with `git status --porcelain` after every run.
Enter the shell as `nix develop path:$VENDOR_DIR/nixos-flake#build-kernel
--command <argv>`, never a bare `path:` ref at the repo root. Nix commands need
the sandbox off, because it blocks the daemon socket.

**E1, E2 and E3 are closed. See the status section above.**

**E4, the realistic worktree.** Every experiment so far used a detached
checkout at the exact built commit. The developer worktree is a tree a human is
editing. Run the chosen route against a dirty worktree and against one at a
different HEAD, and record what kbuild does.

## Answered since, by observation rather than reasoning

**Q1 is closed, and it inverted the plan.** The user opened a real editor and
got `macros::kunit_tests: proc macro server error: Cannot create expander for
.../libmacros.so: No such file or directory (macro-error)`. So the absence of
the dylibs is not a quiet 3.3% shortfall, it is a diagnostic at every macro
site. Step 4 went from "probably not worth writing" to written, with
`proc_macros` defaulting on.

**Q2 is closed, and the answer was better than the question.** No editor
setting is needed. `rust-analyzer` reads `sysroot` from `rust-project.json` and
starts `<sysroot>/libexec/rust-analyzer-proc-macro-srv` itself. Measured with a
1.91.1 client against the 1.95.0 toolchain, no configuration at all: 561
modules, 104,660 declarations, the complete index. The reproducibility is
structural, since one devShell writes both the `sysroot` field and the dylibs.

**The corollary, and it is the sharper half.** An explicit
`rust-analyzer.procMacro.server` in a global editor configuration is harmful.
It applies to every Rust workspace, and it was measured breaking three
proc-macro crates in an ordinary Cargo project at `~/src/buildme` with
`mismatched ABI expected: rustc 1.95...`. The correct server is a property of
the workspace, not of the user; one machine legitimately needs several at once,
and only per-index discovery delivers that.

**The editor-devShell question is therefore closed too: do not build one.** A
generated per-worktree `.helix/languages.toml` was the strongest rival and
would not have dirtied the tree, since the kernel's `.gitignore:13` is a
blanket `.*` and `git check-ignore` confirms `.helix/` is ignored. It was
rejected because it could only transcribe a value the arrangement already
derives. Worth keeping if a future editor ever lacks `rust-project.json`
discovery: Helix reads `<workspace>/.helix/languages.toml`, but its config
merge cuts off at depth 3, so any layer mentioning one key under a language
server's `config` replaces the whole blob and destroys Helix's own defaults.

**A real bug surfaced on the way.** `rust-lib-src`, which backs four of the 46
crates, had zero GC roots, so `nix store gc` would break every kernel Rust
index on the host. The devShell exposes it as an environment variable rather
than a package, so nothing rooted it. Fixed by rooting what the index names.

## Independent questions, same session

**Q1, CLOSED (see above), the editor-visible failure mode.** With three dangling
`proc_macro_dylib_path` entries, `analysis-stats` reports 101,200 declarations
against 104,659, but nobody has watched a real editor. Does rust-analyzer show a
per-crate error banner, or quietly leave `module!` unexpanded? This decides
whether plan step 4, the opt-in `make rust/`, is worth writing at all.

**Q2, CLOSED (see above), the proc-macro server contract.** This host's
`~/.rustup/.../rust-analyzer-proc-macro-srv` refuses the pinned toolchain's
`libmacros.so` with `mismatched ABI expected: rustc 1.91.1 (ed61e7d7e
2025-11-07), got rustc 1.95.0 (59807616e 2026-04-14)`, and the pinned rustc's
own server at `<sysroot>/libexec/rust-analyzer-proc-macro-srv` loads all three
(8, 12 and 6 macros). That is an exact version-string match, not a range.
Confirm the editor honours `rust-analyzer.procMacro.server` pointed there, since
that setting is the whole remedy and the ADR leans on it.

Note one unresolved contradiction in the evidence: the same experiment that
measured +57 modules from the dylibs reported `rust-analyzer --version` as
1.91.1, which should have refused them per the test above. Resolve which binary
actually expanded them before quoting either number in the docs.

**Q3, the cross-host leg, end to end.** No peer was reachable in any of this, so
`nix copy --from ssh://<peer>` of the enlarged layer followed by regeneration on
the peer was never run. The transport is unchanged and per-file path
independence was verified locally, but run it once on host B: the peer's
store-index symlink resolves, the layer copies, and the regenerated index names
host B's tree and host B's own sysroot.

**Q4, cross-host bindgen determinism.** The seven generated `*.rs` were `cmp`'d
byte-identical between two build paths on **one** host. bindgen 0.72.1 emits no
timestamps and the files carry no host paths, but cross-host determinism was
never tested the way vmlinux and `Module.symvers` were. If it does not hold, two
builders publish two store paths for one kernel identity.

**Q5, the real objtree delta.** The payload bytes are known. What the chosen
route leaves behind in the consumer's build dir (the kconfig host-tool tree
under `build/scripts/`, `.config.old`, `include/config/`, `include/generated/`)
was
never measured. Get it, so the docs quote a true number.

## Do not rediscover these

- `make rust-analyzer` does not build the proc-macro dylibs or the generated
  `.rs`. Both target definitions are prerequisite-free (`Makefile:2224`,
  `rust/Makefile:644`); the payload is `always-y`, reachable only through
  `scripts/Makefile.build`'s default `$(obj)/` goal, which a named goal never
  makes. The target has never had a prerequisite in any upstream revision.
- `make rust/libmacros.so` has no rule. `%.so` is absent from `single-targets`
  (`Makefile:304`) and there is no catch-all. The floor is `make O=<b> rust/` at
  47 seconds, or `make O=<b> prepare`, which is the narrowest goal that produces
  both the generated `.rs` and the three dylibs.
- `make rust/` needs the builder's full flag set, not just `LLVM=1`. With
  `LLVM=1` alone it dies at `clang: error: argument unused during compilation:
  '-nostdlibinc' [-Werror,-Wunused-command-line-argument]`, exactly what
  `f/kernel/build_flags.py:11` documents.
- `include/generated/rustc_cfg` needs no goal of its own. syncconfig emits it as
  a by-product, byte-identical to the builder's
  (`scripts/kconfig/confdata.c:306`, `Makefile:901`).
- The `transfer` devShell resolves `rustc` and `bindgen` from `~/.cargo/bin` at
  1.91.1 and leaves `RUST_LIB_SRC` unset. Never regenerate there.
- `f/common/store.py:119` fnmatches the **basename** only, so extensionless
  literals like `.config`, `auto.conf` and `rustc_cfg` need no new mechanism,
  and a bare `*.so` would sweep `arch/x86/entry/vdso/vdso64.so`.
- `scripts/Makefile.build` soft-includes `auto.conf` with `-include`, which is
  what makes the whole kconfig-free route possible. `ifdef CONFIG_RUST` spans
  `rust/Makefile:50` to `:787`, so it encloses both the cfg variables and the
  `rust-analyzer` target; `auto.conf` satisfies it with no command-line
  override needed.
- The generator's arguments do not have to travel from the builder. They are a
  pure function of the rustc version and `auto.conf`, both of which the
  consumer has locally once the layer ships `auto.conf`.
