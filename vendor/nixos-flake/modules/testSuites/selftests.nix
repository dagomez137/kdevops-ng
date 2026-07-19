# SPDX-License-Identifier: copyleft-next-0.3.1
#
# selftests: Linux kernel in-tree self-tests (kselftest).
#
# Upstream: tools/testing/selftests in the Linux kernel tree.
# https://docs.kernel.org/dev-tools/kselftest.html
#
# The kselftest binaries are version-coupled to the kernel under
# test (built from the same source tree), so unlike the other
# suites the userland does not come from nixpkgs: consumers lay a
# built kselftest install tree (run_kselftest.sh,
# kselftest-list.txt, per-collection dirs) on a writable share
# mounted at the state dir, keyed by kernel release, and this
# module declares the template units that run it.
#
# Two templates, one instance per unit of work. `kselftest@` runs
# one collection (`run_kselftest.sh --collection`);
# `kselftest-test@` runs one COLLECTION:TEST list entry
# (`run_kselftest.sh --test`). A collection name may carry `/`
# (net/forwarding) or `-` (cpu-hotplug), so instances are
# systemd-escaped and %I restores the literal name. Both pass
# --no-error-on-fail where the tree's runner supports it: the
# KTAP the unit journals carries the per-test verdicts, so the
# exit status is left to report only infrastructure errors (an
# unknown collection, a missing runner), never a test failure.
# An old tree's runner predates the flag; there a failing test
# also fails the unit, and the KTAP still names the verdict.
{ pkgs, lib, ... }:
let
  # run_kselftest.sh and its per-kernel install tree live under
  # this directory: <stateDir>/<uname -r>/tree/. The tree must be
  # writable (tests run chdir'ed into their collection dir and
  # write there), so consumers mount a writable share here and
  # copy the built tree in; extra runner flags arrive via the
  # kselftest.env EnvironmentFile beside it.
  stateDir = "/var/lib/kselftests";
  documentation = [ "https://docs.kernel.org/dev-tools/kselftest.html" ];
  # --summary sends each test's own output to <tree>/output.log
  # instead of the runner's default /dev/stdout logfile: under
  # systemd, stdout is journald's stream socket, which cannot be
  # reopened by the runner's append redirections (open(2) on a
  # socket fails with ENXIO), so without it every test dies before
  # running. The KTAP skeleton (plan, result lines, totals) still
  # reaches the journal on the inherited stdout; the per-test
  # detail lands beside the tree on the share.
  serviceCommon = {
    Type = "oneshot";
    WorkingDirectory = "-${stateDir}/%v/tree";
    EnvironmentFile = "-${stateDir}/kselftest.env";
    StandardOutput = "journal+console";
    StandardError = "journal+console";
    SyslogIdentifier = "kselftest";
    # Type=oneshot already disables the start timeout; keep it
    # explicit so nobody bounds a run here. The runner bounds each
    # individual test itself (the per-collection `settings`
    # timeout), and the caller's own deadline bounds the whole run.
    TimeoutStartSec = "infinity";
  };
  # The runner is version-coupled to the tree like the tests: an old
  # tree's run_kselftest.sh rejects options it predates with its usage
  # text before running anything (--no-error-on-fail and
  # --override-timeout are both younger than --summary and the
  # collection selectors). Probe the vintage's own usage and pass an
  # optional flag, hardcoded or arriving via KSELFTEST_ARGS, only when
  # that runner knows it, so one unit template drives every kernel a
  # sweep or bisect may boot.
  runKselftest = pkgs.writeShellScript "run-kselftest-compat" ''
    tree=$1
    shift
    usage=$("$tree/run_kselftest.sh" --help 2>&1 || true)
    args=("$@")
    case $usage in
      *--no-error-on-fail*) args+=(--no-error-on-fail) ;;
    esac
    case $usage in
      *--summary*) args+=(--summary) ;;
    esac
    skip=
    for arg in $KSELFTEST_ARGS; do
      if [ -n "$skip" ]; then
        skip=
        continue
      fi
      case $arg in
        -o | --override-timeout)
          case $usage in
            *--override-timeout*) args+=("$arg") ;;
            *) skip=1 ;;
          esac
          ;;
        *) args+=("$arg") ;;
      esac
    done
    exec "$tree/run_kselftest.sh" "''${args[@]}"
  '';
  unitCommon = {
    inherit documentation;
    # The runner and the tests invoke system tools by bare name.
    path = [ "/run/current-system/sw" ];
    # Programmatic re-runs may exceed the default start rate limit.
    unitConfig.StartLimitIntervalSec = 0;
  };
in
{
  environment.systemPackages = with pkgs; [
    perf-tools

    numactl

    libcap
    libseccomp
    keyutils

    iproute2
    ethtool

    # The net selftests' shared shell libraries (net/lib.sh,
    # net/forwarding/lib.sh) parse iproute2 JSON output with jq.
    jq

    # The modern net driver tests are Python (kselftest/ksft.py).
    # lowPrio so a sibling suite's wrapped python3 wins the
    # system-path merge when both compose into one closure.
    (lib.lowPrio python3)

    # kselftest/runner.sh prefixes test output through prefix.pl
    # when Perl is present (unbuffered, per byte); the sed
    # fallback line-buffers.
    perl
  ];

  systemd.services."kselftest@" = unitCommon // {
    description = "Kernel selftests collection %I";
    serviceConfig = serviceCommon // {
      ExecStart = "${runKselftest} ${stateDir}/%v/tree --collection %I";
    };
  };

  systemd.services."kselftest-test@" = unitCommon // {
    description = "Kernel selftest %I";
    serviceConfig = serviceCommon // {
      ExecStart = "${runKselftest} ${stateDir}/%v/tree --test %I";
    };
  };

  systemd.tmpfiles.rules = [
    # The units' WorkingDirectory and EnvironmentFile resolve even
    # before a share is mounted here; over a mounted share the rule
    # is a harmless no-op.
    "d ${stateDir} 0755 root root -"

    # kselftest/runner.sh hard-codes /usr/bin/timeout for its
    # per-test watchdog and silently runs tests unbounded when the
    # path is absent, and kselftest/module.sh hard-codes
    # /sbin/modprobe (the module-driven tests skip without it).
    # NixOS ships neither path, so declare the compat symlinks;
    # same pattern for the many test scripts whose shebang is
    # #!/bin/bash.
    "L+ /usr/bin/timeout - - - - ${pkgs.coreutils}/bin/timeout"
    "L+ /sbin/modprobe   - - - - ${pkgs.kmod}/bin/modprobe"
    "L+ /bin/bash        - - - - ${pkgs.bash}/bin/bash"

    # The firmware collection's fw_namespace helper mounts a tmpfs
    # onto /lib/firmware without creating it and runs before every
    # shell test, so the mount point must exist as a directory (the
    # tmpfs makes it writable). The shell tests use their own temp
    # dirs, so nothing needs to live here.
    "d /lib/firmware 0755 root root -"
  ];
}
