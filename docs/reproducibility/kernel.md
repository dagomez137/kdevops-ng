# R0 — build reproducibility (tinyconfig+DWARF, v7.1-rc7, gcc 15.2.0 via nix build-kernel)

## Verdict
Run + debug layers are byte-identical across REAL hosts (beta1 PASSED: hz-debian
Debian/nix2.34 == hetzie NixOS/nix2.31, vmlinux d2005899.., bzImage 2b77ef27..,
build-id caa42a61..) IFF THREE neutralizations are applied -- ALL rooted in the
build-kernel devShell's CWD-derived $out:
  1. -fdebug-prefix-map=<worktree-abs>/=                 (comp_dir -> constant "build")
  2. pin -frandom-seed (strip nix's -frandom-seed=hash($out) from NIX_CFLAGS_COMPILE,
     set a constant) -- THIS was the actual beta1 blocker, not in the handoff.
  3. pin $out in NIX_LDFLAGS (-L$out/lib) and NIX_CFLAGS_COMPILE.

beta2 PASSED: build-kernel toolchain store paths identical on A and B
(gcc 788mx070.., ld mbyy19md.., ccache c9wwl7s5..) from the same flake.lock,
despite different host OS / nix version. The Nix devShell store hash is a portable
toolchain identity.

## Production implication
Fixes 2 and 3 belong in the nixos-flake build-kernel devShell (make $out
deterministic, or strip the stdenv-injected -frandom-seed / -L$out/lib), NOT as
per-build env hacks. Fix 1 (comp_dir map) is a kernel make-flag (KCFLAGS/KAFLAGS),
keyed on the per-build worktree path. T-fdebug-prefix-map alone is necessary but
NOT sufficient; the devShell $out leak was the real cross-host blocker.

Proof (r0c.sh, two worktrees at different abs prefixes + different $out):
  control: hostA vmlinux/bzImage/build-id ALL differ from hostB
  fixed  : hostA == hostB byte-identical (vmlinux 92fc.., bzImage 5676.., build-id 2862..)
  residual host-specific path leaks in fixed vmlinux: none

## Causal chain (single root)
abs build-dir path in DWARF .debug_str/.debug_line_str (DW_AT_comp_dir)
  -> debug sections differ -> linker --build-id (SHA1 over output) differs
  -> kernel embeds build-id in .rodata -> .rodata differs -> bzImage differs.
The run layer is therefore NOT independently reproducible; the debug-path fix is
load-bearing for the run layer too (refines the handoff's "expect run layer identical").

## Mechanism corrections vs handoff
- Map target is the WORKTREE prefix ($WT/=), NOT the build dir. GCC will not remap
  comp_dir when the map equals the dir exactly (it needs a trailing-sep parent prefix);
  and across hosts the build dir name is constant ("build"), only the home prefix varies.
- Exactly ONE map. Multiple -fdebug-prefix-map fight (GCC consults the last-specified
  first), leaving truncated paths like "/build-a2".
- NEW hazard (not in handoff): build-kernel mkShell exports out=$CWD/outputs/out and
  NIX_LDFLAGS=-L$out/lib; that path lands in kernel .rodata. Same-host it is constant
  (hidden); cross-host it differs -> non-reproducible run layer. Pin or strip it.

## beta1 defconfig+modules (REAL config) — FULL PASS cross-host
hz-debian == hetzie, x86_64 defconfig + DWARF + modules, with the 3 neutralizations:
  run   : bzImage 5eb308bb.. + all 12 *.ko (manifest 98bf7939..) identical
  debug : vmlinux f875dbbc.., build-id a9018170.. identical
  devel : Module.symvers 2fb290b6.. identical; sample .o.cmd has NO host-abs path
          (devel layer relocatable -> mode-alpha fetch viable)
  release 7.1.0-rc7, System.map identical.
MODULE_SIG is off in defconfig, so module signing (T-modsig) is untested here.

## alpha1 (mode-alpha LSP on fetched devel layer) — PASS
B built defconfig; A fetched ONLY the devel layer (no .o/.ko/vmlinux), regenerated
compile_commands.json locally, and a recorded gcc command syntax-checked clean
(rc=0) from A's build dir:
  - compile_commands "file" -> A's own source (/.../hostA/linux/init/main.c);
    includes relative (-I../arch/x86/include, -I./arch/x86/include/generated).
  - ZERO remap needed for header resolution.
  - The only B-path in the .cmd is the inert -fdebug-prefix-map=<B-worktree>/= flag
    (the comp_dir map B used); harmless for LSP, optionally stripped on regen.
  - clangd-without-devShell caveat: NIX_CFLAGS_COMPILE (-isystem nix paths) is env,
    not in .cmd; the test ran inside A's devShell. Real clangd must run in the
    devShell OR compile_commands must bake the -isystem paths. (open)
Devel-layer definition (mode-alpha fetch set), defconfig ~200M, dominated by .cmd
(~174M); MUST exclude vmlinux.unstripped/.tmp_vmlinux*/.bin/bzImage/*.o/.ko/.a.

## Caveats / still to test
- tinyconfig (no modules); R0b needed for modules byte-identity + the run/debug/devel split.
- same-host sim shares the toolchain trivially; real host B (beta1/beta2) must confirm
  toolchain parity (.comment carries the gcc nix store path) and identical flake.lock.
- randstruct seed, module signing, CONFIG_DEBUG_EFI: separate sub-experiments (not yet run).

## Evidence files
r0a.sh, r0a-fix.sh, r0c.sh, r0c.log, r0c-host{A,B}-{ctl,fix}.log, results/r0a-*.txt
