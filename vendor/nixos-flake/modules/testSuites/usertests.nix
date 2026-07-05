# SPDX-License-Identifier: copyleft-next-0.3.1
#
# usertests: the Linux kernel's userspace test harnesses.
#
# Upstream: tools/testing/{radix-tree,vma,rbtree,memblock,scatterlist}
# in the Linux kernel tree: kernel sources (lib/xarray.c,
# lib/maple_tree.c, mm/vma.c, mm/memblock.c, ...) compiled into plain
# userspace binaries through the tools/testing/shared shim. They test
# the source tree they were built from, not the booted kernel; the
# guest only hosts the run.
#
# Consumers lay the built binaries on a writable share under the state
# dir, keyed by kernel release, and this module declares the template
# unit that runs one binary per instance. The instance is the
# systemd-escaped <dir>/<binary> item (radix-tree/main), restored by
# %I both in the executable path and in the per-instance
# EnvironmentFile, which carries the binary's own arguments (only some
# take any). The binaries are built with AddressSanitizer and
# UndefinedBehaviorSanitizer hardwired, so the sanitizer policy is
# pinned here rather than left to defaults: ASan aborts on error,
# UBSan halts on error (it only warns by default), LeakSanitizer
# stays enabled as a gating signal. stdbuf line-buffers stdout because
# the journal socket makes stdio fully buffered and abort() does not
# flush, which would lose a crashing test's context. No LimitAS is
# ever set: ASan's shadow needs unbounded virtual address space.
{ pkgs, ... }:
let
  stateDir = "/var/lib/usertests";
  documentation = [
    "https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/tree/tools/testing"
  ];
in
{
  systemd.services."usertests@" = {
    description = "Kernel userspace test harness %I";
    inherit documentation;
    # Programmatic re-runs may exceed the default start rate limit.
    unitConfig.StartLimitIntervalSec = 0;
    serviceConfig = {
      Type = "oneshot";
      WorkingDirectory = "-${stateDir}/%v/tree";
      EnvironmentFile = "-${stateDir}/%v/env/%I.env";
      Environment = [
        "ASAN_OPTIONS=abort_on_error=1"
        "UBSAN_OPTIONS=halt_on_error=1"
        "LSAN_OPTIONS=detect_leaks=1"
      ];
      ExecStart = "${pkgs.coreutils}/bin/stdbuf --output=L ${stateDir}/%v/tree/%I $ARGS";
      StandardOutput = "journal+console";
      StandardError = "journal+console";
      SyslogIdentifier = "usertests";
      # Type=oneshot already disables the start timeout; keep it
      # explicit so nobody bounds a run here. The caller's own
      # deadline bounds the whole run.
      TimeoutStartSec = "infinity";
    };
  };

  systemd.tmpfiles.rules = [
    # The unit's WorkingDirectory and EnvironmentFile resolve even
    # before a share is mounted here; over a mounted share the rule
    # is a harmless no-op.
    "d ${stateDir} 0755 root root -"
  ];
}
