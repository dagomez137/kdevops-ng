# SPDX-License-Identifier: copyleft-next-0.3.1
#
# kvcache: KV cache characterisation of an LLM server. vLLM serves a
# model with LMCache as its KV connector, and kvcache-bench replays
# recorded multi-turn agent sessions against it.
#
#   vllm-serve.service        the server, configured from the state dir
#   vllm-ready.service        oneshot that returns once /health answers
#   kvcache@<preset>.service  one replay; Requires= pulls the server up
#
# Starting kvcache@<preset> is the only start a run needs: systemd starts
# the server and holds the replay until the server answers. The server
# listens on 127.0.0.1:8000, where vllm-ready probes and kvcache-bench
# connects; the port is set here, so a config that names another one
# loses to it.
#
# Everything a run varies arrives in the state directory rather than in the
# closure, so changing the model, the cache backend or the replay needs no
# rebuild:
#
#   vllm-serve.yaml   vLLM's serve arguments (`vllm serve --config`)
#   vllm-serve.env    the server's environment (LMCACHE_CONFIG_FILE, ...)
#   lmcache.yaml      LMCache's configuration
#   kvcache.env       the replay's KVBENCH_* settings
#
# Results land in <stateDir>/<kernel release>/results/<preset>, so a
# closure booted with different kernels keeps each kernel's results apart.
# Models download into <stateDir>/huggingface and are kept for the next
# boot. A replay of a few sessions streams the trace dataset instead, so it
# reaches the network every run; the revision it reads is pinned in
# kvcache.env either way.
#
# vLLM, torch and LMCache come from pkgsUnstable, the one Python
# environment vLLM imports the LMCache connector from. vLLM runs on the
# CPU, where LMCache has no connector and falls back to recomputing. The
# gpu profile would make them the CUDA builds, but nixpkgs marks vLLM's
# CUDA build broken, so the two are refused together until an overlay
# fixes it.
#
# Source: https://github.com/vllm-project/vllm
# Source: https://github.com/LMCache/LMCache
{ config, pkgs, ... }:
let
  stateDir = "/var/lib/kvcache";
  python = pkgs.pkgsUnstable.python313.withPackages (ps: [
    ps.vllm
    pkgs.lmcache
  ]);
in
{
  assertions = [
    {
      assertion = !(config.nixpkgs.config.cudaSupport or false);
      message = "testSuites.kvcache: nixpkgs marks vLLM's CUDA build broken, so it cannot be composed with profiles.gpu yet.";
    }
  ];

  environment.systemPackages = [
    python
    pkgs.kvcache-bench
    pkgs.curl
  ];

  systemd.services.vllm-serve = {
    description = "vLLM OpenAI-compatible server";
    documentation = [ "https://docs.vllm.ai/en/latest/configuration/serve_args.html" ];
    # The CPU backend reads the topology with lscpu.
    path = [ pkgs.util-linux ];
    # Its compile cache would otherwise land in root's home, which a guest
    # may share with the host.
    environment = {
      HF_HOME = "${stateDir}/huggingface";
      VLLM_CACHE_ROOT = "${stateDir}/vllm-cache";
    };
    serviceConfig = {
      # vLLM does not notify readiness, so the unit is active while the
      # model still loads; vllm-ready is the readiness gate.
      Type = "exec";
      EnvironmentFile = "-${stateDir}/vllm-serve.env";
      ExecStart = "${python}/bin/vllm serve --config ${stateDir}/vllm-serve.yaml --host 127.0.0.1 --port 8000";
      WorkingDirectory = stateDir;
      # A server that dies mid-replay must fail the replay, not come back
      # and serve the rest of it from a cold cache.
      Restart = "no";
      StandardOutput = "journal+console";
      StandardError = "journal+console";
      SyslogIdentifier = "vllm-serve";
    };
  };

  systemd.services.vllm-ready = {
    description = "Wait for the vLLM server to answer /health";
    documentation = [ "https://docs.vllm.ai/en/latest/serving/openai_compatible_server.html" ];
    # Bound, not just required: a server that dies while loading must fail
    # this gate, and the replays waiting on it, rather than leave it probing
    # a dead port for a day.
    bindsTo = [ "vllm-serve.service" ];
    after = [ "vllm-serve.service" ];
    serviceConfig = {
      Type = "oneshot";
      # Stays active once the server answered, so later replays start
      # without probing again.
      RemainAfterExit = true;
      # A day of 5 s retries: the first start downloads the model, which can
      # take hours, and whoever starts a replay owns the deadline.
      ExecStart = "${pkgs.curl}/bin/curl --silent --show-error --fail --retry 17280 --retry-delay 5 --retry-all-errors http://127.0.0.1:8000/health";
      TimeoutStartSec = "infinity";
      StandardOutput = "journal+console";
      StandardError = "journal+console";
      SyslogIdentifier = "vllm-ready";
    };
  };

  systemd.services."kvcache@" = {
    description = "KV cache replay of preset %i";
    documentation = [ "https://huggingface.co/datasets/sammshen/lmcache-agentic-traces" ];
    requires = [ "vllm-ready.service" ];
    after = [ "vllm-ready.service" ];
    environment.HF_HOME = "${stateDir}/huggingface";
    # Programmatic re-runs of one preset may exceed systemd's default
    # start rate limit (5 starts per 10 s).
    startLimitIntervalSec = 0;
    serviceConfig = {
      Type = "oneshot";
      EnvironmentFile = "-${stateDir}/kvcache.env";
      ExecStart = "${pkgs.kvcache-bench}/bin/kvcache-bench --preset %i --output ${stateDir}/%v/results/%i";
      WorkingDirectory = stateDir;
      # A whole-dataset replay runs for hours; whoever starts it owns the
      # deadline.
      TimeoutStartSec = "infinity";
      StandardOutput = "journal+console";
      StandardError = "journal+console";
      SyslogIdentifier = "kvcache";
    };
  };

  # The units' paths resolve before the state directory's share is mounted
  # over it.
  systemd.tmpfiles.rules = [ "d ${stateDir} 0755 root root -" ];
}
