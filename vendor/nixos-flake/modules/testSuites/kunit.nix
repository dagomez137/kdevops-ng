# SPDX-License-Identifier: copyleft-next-0.3.1
#
# kunit: drive the kernel's KUnit suites through debugfs on a booted
# kernel and capture their KTAP to the journal. See
# Documentation/dev-tools/kunit/.
#
# Two templates. `kunit@<suite>` re-runs a suite by writing its debugfs
# `run` node; the write is strict, so a failed trigger fails the unit
# instead of collecting a stale `results` as if it were a fresh run.
# `kunit-results@<suite>` only reads back the results a suite left from
# its boot-time run (an init-only suite has no `run` node).
#
# A suite built as a module registers only once its module is loaded.
# The set of KUnit modules is a property of the booted kernel, so no
# static list can declare it: kunit-test-modules.service creates the
# list at boot, modeled on systemd's kmod-static-nodes.service (a
# oneshot that derives a runtime config from /lib/modules/%v for
# another service to consume). It scans the modules for the
# `.kunit_test_suites` ELF section and writes every match to
# /run/modules-load.d/kunit.conf before systemd-modules-load.service
# reads its configuration, per modules-load.d(5). `kunit.modules`
# remains for extra names beyond the scan.
{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.nixos-flake.kunit;
  documentation = [ "https://docs.kernel.org/dev-tools/kunit/run_manual.html" ];
  serviceCommon = {
    Type = "oneshot";
    StandardOutput = "journal+console";
    StandardError = "journal+console";
    SyslogIdentifier = "kunit";
    # Type=oneshot already disables the start timeout; keep it explicit
    # so nobody bounds a suite here. A hung suite hangs inside the
    # kernel, where only the caller's own deadline can act.
    TimeoutStartSec = "infinity";
  };
  unitCommon = {
    inherit documentation;
    # Programmatic re-runs of one suite may exceed the default start
    # rate limit (5 starts per 10 s).
    unitConfig.StartLimitIntervalSec = 0;
  };
  # A test module is one whose ELF carries the `.kunit_test_suites`
  # section (the kunit_test_suites() registration macro emits it), so
  # the section-name string in the module file is the detector; grep
  # counts instead of quitting on the first match so a compressor
  # upstream of the pipe never dies on SIGPIPE under pipefail.
  kunitTestModules = pkgs.writeShellApplication {
    name = "kunit-test-modules";
    runtimeInputs = with pkgs; [
      coreutils
      findutils
      gnugrep
      gzip
      xz
      zstd
    ];
    text = ''
      moddir="/lib/modules/$1"
      conf="/run/modules-load.d/kunit.conf"
      mkdir --parents /run/modules-load.d
      : > "$conf"
      count=0
      while IFS= read -r -d "" module; do
        case "$module" in
          *.ko) reader=(cat) ;;
          *.ko.xz) reader=(xz --decompress --stdout) ;;
          *.ko.zst) reader=(zstd --decompress --stdout --quiet) ;;
          *.ko.gz) reader=(gzip --decompress --stdout) ;;
          *) continue ;;
        esac
        matches="$("''${reader[@]}" "$module" \
          | grep --count --text --fixed-strings .kunit_test_suites)" || true
        if [ "''${matches:-0}" -gt 0 ]; then
          name="$(basename "$module")"
          printf '%s\n' "''${name%%.ko*}" >> "$conf"
          count=$((count + 1))
        fi
      done < <(find "$moddir" -name '*.ko*' -print0)
      echo "declared $count KUnit module(s) in $conf"
    '';
  };
in
{
  options.nixos-flake.kunit = {
    modules = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ ];
      example = [ "kunit-example-test" ];
      description = ''
        Extra kernel modules to load at boot beyond the scanned set,
        through modules-load.d(5). kunit-test-modules.service already
        declares every module of the booted kernel that carries KUnit
        suites, so this is only for names the scan cannot see. A listed
        module the booted kernel does not ship fails
        systemd-modules-load.service and degrades the boot.
      '';
    };
  };

  config = {
    boot.kernelModules = cfg.modules;

    # Create the list of the booted kernel's KUnit test modules before
    # modules-load reads its configuration; /lib/modules is mounted by
    # the initrd, so the scan has its input this early. The shape and
    # conditions mirror kmod-static-nodes.service: without module
    # support or a modules tree for the running kernel (%v) there is
    # nothing to list.
    systemd.services.kunit-test-modules = {
      description = "Create List of KUnit Test Modules";
      inherit documentation;
      wantedBy = [ "sysinit.target" ];
      before = [
        "sysinit.target"
        "systemd-modules-load.service"
      ];
      unitConfig = {
        DefaultDependencies = false;
        ConditionCapability = "CAP_SYS_MODULE";
        ConditionPathIsDirectory = "/lib/modules/%v";
      };
      serviceConfig = {
        Type = "oneshot";
        RemainAfterExit = true;
        ExecStart = "${lib.getExe kunitTestModules} %v";
        SyslogIdentifier = "kunit";
      };
    };

    systemd.services."kunit@" = unitCommon // {
      description = "KUnit suite %i";
      serviceConfig = serviceCommon // {
        StandardInputText = "1";
        ExecStart = [
          "${pkgs.coreutils}/bin/tee /sys/kernel/debug/kunit/%i/run"
          "${pkgs.coreutils}/bin/cat /sys/kernel/debug/kunit/%i/results"
        ];
      };
    };

    systemd.services."kunit-results@" = unitCommon // {
      description = "KUnit suite %i boot-time results";
      serviceConfig = serviceCommon // {
        ExecStart = "${pkgs.coreutils}/bin/cat /sys/kernel/debug/kunit/%i/results";
      };
    };
  };
}
