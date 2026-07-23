.. SPDX-License-Identifier: copyleft-next-0.3.1

:orphan:

==========
Monitoring
==========

The optional monitoring stack runs `Grafana`_, `Prometheus`_, and `Loki`_
beside the Windmill instance, from the same Nix flake and under the same
``systemd --user`` model as the :doc:`Nix deployment </deployment/nix>`.
Guests push their metrics and journal to it; dashboards, datasources, and a
run-annotation API make test runs explorable against the system state they
ran under. The stack is optional in both directions: the Windmill units never
reference it, and deactivating it leaves the instance untouched.

The data flows in, not out. Every guest runs one `Grafana Alloy`_ agent that
pushes metrics over Prometheus remote_write and its journal over the Loki
push API to the host side of its user-mode network, so any number of
short-lived guests report to the two loopback ports below with no per-guest
registration on the host (see the architecture decision record
``notes/adr/0011-guest-telemetry-push-over-slirp.md``). Prometheus therefore
runs with its remote-write receiver enabled and an empty scrape list.

Build, install, activate
========================

``nix run .#monitoring-deploy`` does the whole sequence; the three steps also
run on their own, mirroring the Windmill apps:

.. code-block:: console
   :caption: host
   :class: cmd-host

   $ nix run .#monitoring-build      # out-links under the state dir
   $ nix run .#monitoring-install    # units, configs, provisioning
   $ nix run .#monitoring-activate   # enable --now the three services

Four units land: ``monitoring-grafana.service``,
``monitoring-prometheus.service``, ``monitoring-loki.service``, and the
``monitoring-db-setup.service`` oneshot Grafana pulls in. All bind loopback
only. Reach the UI over an SSH forward, exactly like the Windmill UI:

.. code-block:: console
   :class: cmd-host

   $ ssh -L 3000:localhost:3000 <host>

then browse ``http://localhost:3000`` (first login ``admin``/``admin``;
Grafana forces a password change).

Grafana
=======

:cmd:`grafana` keeps its own state (users, tokens, dashboard edits) in a
``grafana`` database inside the Windmill PostgreSQL cluster.
``monitoring-db-setup.service`` provisions it over the cluster's local
socket: a ``grafana`` role with a generated password (under
``~/.local/state/monitoring/secrets/``), the database, and the
``GF_DATABASE_*`` environment file the Grafana unit reads. It is a separate
oneshot rather than an ``ExecStartPre`` because ``systemd`` loads a service's
``EnvironmentFile=`` before any of its Exec lines run; only unit ordering can
guarantee the file exists first.

Everything declarative about Grafana is provisioned as code from
``deploy/nix/monitoring/grafana/``: the Prometheus and Loki datasources, and
every dashboard JSON under ``deploy/nix/monitoring/dashboards/``. Git is the
source of truth; edit the JSON in the repository and re-run
``nix run .#monitoring-install``, not the UI (UI edits to provisioned
dashboards are disabled). Per-setting overrides go in
``~/.config/monitoring/monitoring-grafana.env`` as ``GF_<SECTION>_<KEY>``
lines.

The shipped ``Guest overview`` dashboard shows CPU, memory, load, disk and
network I/O, and the journal per guest. Because guests push, a dead guest
goes stale rather than flipping an ``up`` metric; the dashboard's freshness
panel shows seconds since the last received sample per host, which is the
liveness signal.

Prometheus
==========

:cmd:`prometheus` listens on ``127.0.0.1:9090`` with
``--web.enable-remote-write-receiver``: the guests' Alloy agents push to
``/api/v1/write``, so the shipped ``prometheus.yml`` has an empty scrape
list. The TSDB lives under ``~/.local/state/monitoring/prometheus`` with 30
days retention; override either by editing the unit
(``systemctl --user edit monitoring-prometheus.service``).

Loki
====

:cmd:`loki` listens on ``127.0.0.1:3100`` and stores chunks and index on the
filesystem under ``~/.local/state/monitoring/loki`` (single-binary mode).
Guests push their journal to ``/loki/api/v1/push`` with ``host`` and ``unit``
labels. One tuning ships in ``loki.yaml``: the WAL disk-full protection
throttles every push once the filesystem crosses a usage fraction, and its
0.90 default rejects all logs on a build host that routinely runs above 90%
of a large disk, so the shipped config raises it to 0.95. Loki's flag parser
has no long-form spellings, so its single-dash flags are the documented
exception to the long-form rule.

Run annotations
===============

Suite flows post a region annotation to Grafana spanning each run's exact
window, tagged ``suite-run`` plus the suite, guest, kernel, and verdict, so
the ``Guest overview`` dashboard shows what ran under the metrics. The
endpoint lives in the Windmill resource ``f/monitoring/grafana``, which is
in git and synced like the rest of the workspace; its ``token`` field is a
``$var:f/monitoring/grafana_token`` reference, because variables are the
one workspace kind the sync never touches (``skipVariables`` in
``wmill.yaml``), so the credential survives every push. Until the operator
creates that variable, runs simply skip the annotation with a note in the
job log.

Mint the credential as a Grafana service account (role ``Editor``) with one
token; both are one-time actions, over the SSH forward or on the host:

.. code-block:: console
   :class: cmd-host

   $ curl --silent --user admin:<password> \
       --header 'Content-Type: application/json' \
       --request POST http://127.0.0.1:3000/api/serviceaccounts \
       --data '{"name":"kdevops-annotations","role":"Editor"}'
   $ curl --silent --user admin:<password> \
       --header 'Content-Type: application/json' \
       --request POST http://127.0.0.1:3000/api/serviceaccounts/<id>/tokens \
       --data '{"name":"kdevops-annotations"}'

then store the returned key as the secret Windmill variable
``f/monitoring/grafana_token`` (Windmill UI: Variables, Add variable,
secret). A non-local Grafana only needs the resource's ``base_url``
changed, in git.

Multiple hosts and baremetal
============================

A worker host whose guests should report to a primary monitoring host
elsewhere runs the fan-in instead of the full stack:
``nix run .#monitoring-collector-install`` installs a host-side
:cmd:`alloy` (``monitoring-collector.service``) listening on the same two
loopback endpoints the guests already push to, so their zero-config URLs
keep working, and forwarding everything, ``host`` labels intact, to the
primary. Transport matches the deployment's peer-SSH posture:
``monitoring-tunnel.service`` keeps a persistent :cmd:`ssh` forward
(local ports 19090 and 13100) to the primary's loopback stack, and the
collector's two ``PRIMARY_*`` URLs point at those forward ports. Both
units read their settings from required environment files the install
step prints.

A baremetal system needs no collector arrangement of its own: the
``telemetry`` profile applies unchanged, with the closure form's two push
URL fields pointed at the monitoring host (the ``10.0.2.2`` defaults are
the QEMU user-network case only).

Deactivate
==========

.. code-block:: console
   :class: cmd-host

   $ nix run .#monitoring-deactivate

stops and disables the ``monitoring-*`` units. State (the Prometheus TSDB,
Loki chunks, Grafana's database and secrets) stays in place for the next
activation.

.. _Grafana: https://grafana.com/oss/grafana/
.. _Prometheus: https://prometheus.io/
.. _Loki: https://grafana.com/oss/loki/
.. _Grafana Alloy: https://grafana.com/oss/alloy/
