# SPDX-License-Identifier: copyleft-next-0.3.1
#
# Continuous telemetry export from the guest.
#
# One Grafana Alloy agent pushes the system's metrics and journal
# to remote collectors for the whole life of the boot. This is the
# complement of profiles/monitoring.nix, not a replacement: that
# module's monitor-<name>@<run-id> template units bracket a single
# workload and write files an orchestrator collects, while this
# one exports continuously so dashboards see every guest live.
#
# The agent is a single alloy.service wiring four components:
# prometheus.exporter.unix (the embedded node_exporter) scraped by
# prometheus.scrape into prometheus.remote_write, and
# loki.source.journal into loki.write. Every series and log line
# carries a host label from the system hostname, so a fleet of
# guests pushing to one collector stays distinguishable.
#
# The module is deliberately endpoint-agnostic: metrics.url and
# logs.url have no defaults, because where the collectors live is
# the consumer's topology (a hypervisor address, a monitoring
# host, a local aggregator). Both are Alloy push targets, so the
# collectors need no knowledge of the guests; a guest that boots,
# pushes, and disappears needs no registration anywhere.
#
# Alloy keeps its WAL under /var/lib/alloy. On the imageless
# backend that is tmpfs, so a crashed guest loses at most one
# flush interval of buffered samples; acceptable for test guests,
# and the WAL becomes durable automatically wherever /var/lib is.

{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.nixos-flake.telemetry;

  alloyConfig = ''
    prometheus.exporter.unix "node" {
      enable_collectors = [${lib.concatMapStringsSep ", " (c: ''"${c}"'') cfg.extraCollectors}]
    }

    prometheus.scrape "node" {
      targets         = prometheus.exporter.unix.node.targets
      forward_to      = [prometheus.remote_write.default.receiver]
      scrape_interval = "${cfg.scrapeInterval}"
    }

    prometheus.remote_write "default" {
      endpoint {
        url = "${cfg.metrics.url}"
      }
      external_labels = {
        host = constants.hostname,
      }
    }

    loki.relabel "journal" {
      forward_to = []

      rule {
        source_labels = ["__journal__systemd_unit"]
        target_label  = "unit"
      }
    }

    loki.source.journal "journal" {
      forward_to    = [loki.write.default.receiver]
      relabel_rules = loki.relabel.journal.rules
      labels        = {
        host = constants.hostname,
      }
    }

    loki.write "default" {
      endpoint {
        url = "${cfg.logs.url}"
      }
    }
  '';
in
{
  options.nixos-flake.telemetry = {
    enable = lib.mkEnableOption "continuous metrics and journal export";

    metrics.url = lib.mkOption {
      type = lib.types.str;
      example = "http://192.0.2.1:9090/api/v1/write";
      description = ''
        Prometheus remote_write endpoint the agent pushes metrics
        to. Point it at a Prometheus server started with
        --web.enable-remote-write-receiver, or at any other
        remote_write receiver.
      '';
    };

    logs.url = lib.mkOption {
      type = lib.types.str;
      example = "http://192.0.2.1:3100/loki/api/v1/push";
      description = ''
        Loki push endpoint the agent ships the systemd journal to.
      '';
    };

    scrapeInterval = lib.mkOption {
      type = lib.types.str;
      default = "15s";
      description = ''
        Interval at which the agent scrapes its embedded node
        exporter.
      '';
    };

    extraCollectors = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ ];
      example = [
        "buddyinfo"
        "zoneinfo"
        "meminfo_numa"
      ];
      description = ''
        node_exporter collectors to enable on top of the defaults
        (prometheus.exporter.unix's enable_collectors). The
        memory-management trio buddyinfo, zoneinfo, and
        meminfo_numa exposes the buddy allocator's per-order free
        blocks, per-zone counters, and per-NUMA-node meminfo; all
        node_exporter collector names are accepted.
      '';
    };
  };

  config = lib.mkIf cfg.enable {
    environment.etc."alloy/config.alloy".text = alloyConfig;

    systemd.services.alloy = {
      description = "Grafana Alloy telemetry agent";
      documentation = [ "https://grafana.com/docs/alloy/latest/" ];
      wantedBy = [ "multi-user.target" ];
      after = [ "network-online.target" ];
      wants = [ "network-online.target" ];
      # Re-exec on config change instead of restart: Alloy reloads
      # cleanly and the WAL position survives.
      reloadTriggers = [ config.environment.etc."alloy/config.alloy".source ];

      serviceConfig = {
        ExecStart = "${pkgs.grafana-alloy}/bin/alloy run --storage.path=/var/lib/alloy --disable-reporting /etc/alloy/config.alloy";
        ExecReload = "${pkgs.coreutils}/bin/kill -HUP $MAINPID";
        StateDirectory = "alloy";
        Restart = "on-failure";
        RestartSec = "5s";
      };
    };
  };
}
