// Pure flow glue: merge the typed step results into one manifest. Using
// bun (not hand-built JSON) keeps the output safe regardless of input,
// and sourcing every field from the step results (not flow_input) makes
// the manifest reflect the effective values regardless of config method.
export async function main(
  prepare: any,
  configure: any,
  compile: any,
  devtools: any,
  install: any,
  modules: any,
  reuse: any,
  publish: any,
) {
  return {
    worker: prepare?.worker ?? null,
    git_ref: prepare?.git_ref ?? null,
    commit: prepare?.commit ?? null,
    uts_release: compile?.uts_release ?? configure?.kernelrelease ?? null,
    config_method: configure?.method ?? null,
    defconfig: configure?.defconfig ?? null,
    fragments: configure?.fragments ?? null,
    preset: configure?.preset ?? null,
    targets: compile?.targets ?? null,
    linux_compiler: compile?.linux_compiler ?? null,
    uts_version: compile?.uts_version ?? null,
    uts_machine: compile?.uts_machine ?? null,
    linux_compile_by: compile?.linux_compile_by ?? null,
    linux_compile_host: compile?.linux_compile_host ?? null,
    worktree: prepare?.worktree ?? null,
    build_dir: prepare?.build_dir ?? null,
    config: configure?.config ?? null,
    bzImage: publish?.bzImage ?? compile?.image ?? reuse?.bzImage ?? null,
    destdir: install?.destdir ?? modules?.destdir ?? reuse?.destdir ?? null,
    boot: publish?.boot ?? install?.boot ?? reuse?.boot ?? null,
    modules: publish?.modules ?? modules?.modules ?? reuse?.modules ?? null,
    source: modules?.source ?? null,
    compile_commands: devtools?.compile_commands ?? null,
    vmlinux_gdb: devtools?.vmlinux_gdb ?? null,
    rust_project: devtools?.rust_project ?? null,
  };
}
