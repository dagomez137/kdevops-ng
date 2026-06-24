// Pure flow glue: merge the typed step results into one manifest. Every
// field is sourced from the step results so the manifest reflects the
// effective build, and QEMU/systemd reads the same shape regardless of provider.
export async function main(
  prepare: any,
  identity: any,
  configure: any,
  devtools: any,
  install: any,
  reuse: any,
  publish: any,
) {
  // After a real install, point the emulator at its /nix/store copy (the
  // published tree is the prefix verbatim, so basenames match) so a
  // cross-worker-group boot resolves it; on reuse the manifest already
  // carries the store path, and a build-only run falls back to the destdir.
  const storeBin = (p: string) =>
    publish?.store_path && p ? publish.store_path + "/bin/" + p.split("/").pop() : p;
  return {
    worker: prepare?.worker ?? null,
    qemu_ref: prepare?.qemu_ref ?? null,
    commit: prepare?.commit ?? null,
    version: prepare?.version ?? null,
    target_list: configure?.target_list ?? null,
    compiler: configure?.compiler ?? null,
    identity: identity?.identity ?? null,
    prefix: identity?.prefix ?? null,
    worktree: prepare?.worktree ?? null,
    build_dir: prepare?.build_dir ?? null,
    destdir: install?.destdir ?? identity?.prefix ?? prepare?.destdir ?? null,
    qemu_binary: install?.qemu_binary
      ? storeBin(install.qemu_binary)
      : (reuse?.qemu_binary ?? null),
    qemu_binaries: install?.qemu_binaries
      ? install.qemu_binaries.map(storeBin)
      : null,
    compile_commands: devtools?.compile_commands ?? null,
  };
}
