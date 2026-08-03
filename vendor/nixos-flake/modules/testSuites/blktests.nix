# SPDX-License-Identifier: copyleft-next-0.3.1
#
# blktests: block layer regression tests.
#
# Upstream: https://github.com/linux-blktests/blktests
#
# Ships the blktests package (pkgs/blktests.nix) plus the userland
# its test groups probe with _have_program, and declares the
# blktests@<group>.service template unit so a run is a first-class
# systemd object (one instance per test group), driven and
# inspected remotely with systemctl. The suite's config and the
# check arguments are supplied out-of-band on a host-shared
# directory, so changing the run needs no closure rebuild.
{ pkgs, lib, ... }:
let
  # The blktests@.service reads its config from and writes its
  # results under this directory. Consumers mount a writable share
  # here (the config is laid down before the run; results are read
  # back after). The path is a fixed contract between the unit and
  # whatever drives it.
  stateDir = "/var/lib/blktests";
in
{
  environment.systemPackages = with pkgs; [
    # The test harness itself; check runs from the package tree,
    # which is read-only, and writes only where --output points.
    blktests

    # Block-device userland tools
    nvme-cli
    sg3_utils
    multipath-tools
    dmraid
    lvm2
    mdadm

    # SCSI target framework used by iSCSI and FC test groups
    # (nixpkgs ships the fork as targetcli-fb; the upstream name
    # 'targetcli' has been retired from nixpkgs.)
    targetcli-fb

    # I/O generator and stats used across several groups
    fio
    sysstat

    # Tools individual tests probe with _have_program; absent the
    # binary the affected tests report "not run". gawk and
    # util-linux (blockdev, losetup, blkzone, dmesg) are hard
    # startup dependencies of check itself.
    gawk
    util-linux
    parted
    e2fsprogs
    f2fs-tools
    btrfs-progs
    dosfstools
    xfsprogs
    cryptsetup
    bcache-tools
    nbd
    blktrace
    iproute2
    ethtool
    pciutils
    expect
    bc
    procps
    keyutils
    python3
  ];

  # A few tests drop privileges through the NORMAL_USER config
  # variable (su <user> -c). Declare a dedicated unprivileged
  # account so consumers can point NORMAL_USER at it. The gid is
  # pinned into the 500+ band clear of NixOS' reserved system
  # slots and of the slots the fstests module claims, so both
  # suite modules compose into one closure.
  users.groups.blktests = {
    gid = 506;
  };
  users.users.blktests = {
    isSystemUser = true;
    group = "blktests";
    useDefaultShell = true;
    description = "blktests unprivileged test user";
  };

  # mdadm assembles arrays with udev's help: its rules create the
  # /dev/md/<name> symlink an mdadm --create waits on, and without
  # them in the active ruleset the create times out and the md tests
  # fail. Putting mdadm in systemPackages does not install rules;
  # register the package with udev explicitly.
  services.udev.packages = [ pkgs.mdadm ];

  # The device-mapper tests drive dm through libdevmapper, which
  # serializes operations via a udev cookie semaphore: dmsetup
  # increments it and udevd decrements it from lvm2's dm udev
  # rules. Those rules only land in the active ruleset through
  # services.lvm, which the imageless backend disables by default,
  # so opt back in or dm tests hang in __do_semtimedop waiting on
  # the never-released cookie.
  services.lvm.enable = lib.mkForce true;

  # The blktests run as a first-class systemd unit. %i is a test
  # group name (block, nvme, loop, ...), a valid instance name with
  # no escaping; the unit runs check for exactly that group.
  #
  # Type=oneshot so the start job tracks the whole run and the unit
  # lands a real Result/ExecMainStatus (the check exit code). A
  # caller that does not want to block starts it with --no-block
  # and polls show --property=Result,ExecMainStatus,ActiveState.
  #
  # All tunables live in ${stateDir}/config, the suite's own
  # sourced configuration file, passed via --config so check never
  # looks for one in its read-only working directory. The
  # per-instance ${stateDir}/%i.env EnvironmentFile carries only
  # $BLKTESTS_ARGS, the positional group or test list, written with
  # a single $ so systemd word-splits it. The - prefix tolerates an
  # instance whose env file is absent.
  #
  # Results are keyed by the running kernel's release: %v is the
  # systemd "kernel release" specifier, resolved at unit start, so
  # the same closure booted with different kernels never overwrites
  # another kernel's results. check creates the --output directory
  # itself.
  systemd.services."blktests@" = {
    description = "blktests check (group %i)";
    documentation = [ "https://github.com/linux-blktests/blktests" ];
    # check and the test scripts shell out to bash, gawk, and the
    # block userland by bare name. A service unit otherwise gets
    # only systemd's minimal default PATH, so put the system
    # profile on PATH; it carries everything this module adds to
    # environment.systemPackages.
    path = [ "/run/current-system/sw" ];
    # A driver may restart one instance in quick succession; never
    # rate-limit it.
    startLimitIntervalSec = 0;
    serviceConfig = {
      Type = "oneshot";
      WorkingDirectory = "${pkgs.blktests}/blktests";
      EnvironmentFile = "-${stateDir}/%i.env";
      ExecStart = "${pkgs.blktests}/blktests/check --config=${stateDir}/config --output=${stateDir}/%v/results $BLKTESTS_ARGS";
      StandardOutput = "journal+console";
      StandardError = "journal+console";
      # A full group can run for hours; never let systemd bound the
      # run. The driver owns the deadline and stops the unit on
      # expiry.
      TimeoutStartSec = "infinity";
      SyslogIdentifier = "blktests";
    };
  };

  systemd.tmpfiles.rules = [
    # The blktests@.service config/results dir. Created so the
    # unit's EnvironmentFile resolves even before a share is
    # mounted here; when a writable share is mounted at
    # ${stateDir} this rule is a harmless no-op over the mount
    # point.
    "d ${stateDir} 0755 root root -"
  ];
}
