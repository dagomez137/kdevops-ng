# SPDX-License-Identifier: copyleft-next-0.3.1
#
# kvcache: LLM KV cache characterisation (vLLM serving + LMCache offload +
# agentic-trace replay).
#
# Ships vLLM (nixpkgs), LMCache (pkgs/lmcache.nix) and the trace replayer
# (pkgs/kvcache-bench.nix), and expresses the whole run as systemd units so
# nothing ever starts a process by hand:
#
#   vllm-serve.service        the serving engine (long-running)
#   vllm-ready.service        oneshot gate on a real /health response
#   kvcache-bench@<preset>    the replay run; Requires= pulls the two above
#
# Starting `kvcache-bench@<preset>` is therefore the ONLY start a driver
# issues; systemd brings the engine up and gates on readiness itself.
#
# All tunables arrive out-of-band on ${stateDir} (a host-shared directory,
# tag `kvcache`, same contract as fstests' /var/lib/xfstests): the engine's
# EnvironmentFile and one env file per preset. Changing a model, cache
# backend or preset needs no closure rebuild. Results are keyed by the
# running kernel release (%v), so the same closure booted with different
# kernels never overwrites another kernel's results.
{
  pkgs,
  lib,
  ...
}:
let
  # Fixed contract between these units and whatever drives them (the
  # f/kvcache/* steps): config is laid down here before a run, results are
  # read back from here after.
  stateDir = "/var/lib/kvcache";
in
{
  environment.systemPackages = with pkgs; [
    vllm
    lmcache
    kvcache-bench

    # Storage userland the offload characterisation pokes at.
    nvme-cli
    fio
    blktrace
    sysstat

    pciutils
    python3
    curl
    jq
  ];

  # The serving engine. vLLM does not sd_notify, so Type=exec reports
  # "active" long before the engine can serve; vllm-ready below is the real
  # readiness gate. Restart=no: a crashed engine must surface as a failed
  # dependency of the bench run, not silently respawn mid-measurement.
  systemd.services.vllm-serve = {
    description = "vLLM serving engine (LMCache KV offload)";
    documentation = [ "https://docs.vllm.ai" ];
    path = [ "/run/current-system/sw" ];
    serviceConfig = {
      Type = "exec";
      # ${stateDir}/vllm-serve.env carries VLLM_MODEL, VLLM_SERVE_ARGS and the
      # LMCACHE_* variables; written by the driver (f/kvcache/render_config).
      EnvironmentFile = "${stateDir}/vllm-serve.env";
      ExecStart = "${pkgs.vllm}/bin/vllm serve $VLLM_MODEL $VLLM_SERVE_ARGS";
      WorkingDirectory = stateDir;
      Restart = "no";
      StandardOutput = "journal+console";
      StandardError = "journal+console";
      SyslogIdentifier = "vllm-serve";
      # Large models take many minutes to load; readiness is vllm-ready's
      # job, so never let systemd bound the startup.
      TimeoutStartSec = "infinity";
    };
  };

  # Readiness as a unit rather than a poll loop in a driver script: gate on
  # an actual /health response. RemainAfterExit so later bench instances see
  # it active without re-probing an already-warm engine.
  systemd.services.vllm-ready = {
    description = "Wait for the vLLM engine to accept requests";
    requires = [ "vllm-serve.service" ];
    after = [ "vllm-serve.service" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      ExecStart = "${pkgs.curl}/bin/curl --retry 360 --retry-delay 5 --retry-all-errors --fail --silent --show-error http://127.0.0.1:8000/health";
      TimeoutStartSec = "infinity";
      SyslogIdentifier = "vllm-ready";
    };
  };

  # The replay as a first-class systemd unit. %i is a preset name
  # (swebench-realistic, gaia-burst, ...), a valid instance name with no
  # escaping. Type=oneshot so the start job tracks the whole run and the
  # unit lands a real Result/ExecMainStatus (the replayer's exit code).
  # Requires= pulls the engine and its readiness gate up automatically; a
  # caller that does not want to block starts with --no-block and polls
  # show --property=Result,ExecMainStatus,ActiveState.
  systemd.services."kvcache-bench@" = {
    description = "KV cache trace replay (preset %i)";
    requires = [ "vllm-ready.service" ];
    after = [ "vllm-ready.service" ];
    path = [ "/run/current-system/sw" ];
    startLimitIntervalSec = 0;
    serviceConfig = {
      Type = "oneshot";
      # Per-preset knobs (dataset, workers, session cap, endpoint); the -
      # prefix tolerates an instance whose env file is absent, in which case
      # the replayer's own defaults apply.
      EnvironmentFile = "-${stateDir}/%i.env";
      # Results keyed by kernel release: %v resolves at unit start.
      ExecStart = "${pkgs.kvcache-bench}/bin/kvcache-bench --preset %i --output ${stateDir}/%v/results/%i";
      WorkingDirectory = stateDir;
      StandardOutput = "journal+console";
      StandardError = "journal+console";
      # A full 787-session replay runs for hours; only the caller's own
      # deadline can act.
      TimeoutStartSec = "infinity";
      SyslogIdentifier = "kvcache-bench";
    };
  };

  systemd.tmpfiles.rules = [
    # Created so the units' EnvironmentFile paths resolve even before a
    # share is mounted here; a harmless no-op over the mount point once the
    # writable share is mounted at ${stateDir}.
    "d ${stateDir} 0755 root root -"
  ];
}
