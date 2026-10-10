.. SPDX-License-Identifier: copyleft-next-0.3.1

:orphan:

=========================
Run a KV cache benchmark
=========================

The :src:`f/kvcache/run` flow measures what an LLM server's KV cache does to
time to first token. It serves a model with `vLLM`_, optionally offloads the
KV cache vLLM computes through `LMCache`_ (to CPU memory, or on to a directory
on the share), and replays recorded multi-turn agent sessions against it: each
session's prompts grow by prefix, turn after turn, so a cache that keeps those
prefixes answers later turns sooner. The guest is produced separately by
:src:`f/qsu/bringup` with the ``kvcache`` test suite in the closure and a
writable ``kvcache`` `virtiofs`_ share mounted at ``/var/lib/kvcache``.

The guest side is three `systemd`_ units defined in
:src:`vendor/nixos-flake/modules/testSuites/kvcache.nix`:
``vllm-serve.service`` (the server), ``vllm-ready.service`` (a readiness gate
bound to it) and ``kvcache@<preset>.service`` (one replay, run by the
:src:`kvcache-bench <vendor/nixos-flake/pkgs/kvcache-bench>` replayer). The
flow:

1. :src:`discover <f/kvcache/discover>`: gate the guest over vsock-SSH, check
   that ``/var/lib/kvcache`` is the mounted share, read its replay presets,
   and report whether an NVIDIA GPU reached it and whether its kernel exposes
   NUMA.
2. :src:`render_config <f/kvcache/render_config>`: write the server's
   arguments, LMCache's configuration and the replay's settings onto the host
   side of the share, so changing any of them needs no closure rebuild.
3. for each preset in turn: :src:`start <f/kvcache/start>` →
   :src:`wait <f/kvcache/wait>` → :src:`collect <f/kvcache/collect>`.
   ``start`` stops the server, clears the ``disk`` backend's directory on
   the share, then starts ``kvcache@<preset>.service``, which requires the
   readiness gate, so systemd starts the server afresh, waits for it to
   answer, then replays. Every preset therefore measures from a freshly
   started server with this run's configuration and no cache warmed by an
   earlier preset.
4. :src:`teardown <f/kvcache/stop>`: stop the replays and the server, and
   clear the ``disk`` backend's directory, so a finished run leaves neither
   the server's memory nor the offloaded cache behind.
5. :src:`report <f/kvcache/report>` and :src:`judge <f/kvcache/judge>`: the
   tables, and a red job unless every preset passed.

The guest
=========

A guest with no GPU runs vLLM on its CPU, and that is the one setup run so
far. The form's engine, cache and replay defaults are the ones that run used:
backend ``none``, Qwen2.5-0.5B-Instruct, one session of ``gaia-burst``, each
turn capped at 8 output tokens, and ``GPU Memory Utilization`` 0.5. Its
verdict settings are not: that run judged with a ``Max Failure Rate`` of 1.0
and counted ``context`` failures, and the shipped verdict (0.05, ``context``
left out) has not run live. A CPU engine serves only the baseline: LMCache
has no connector for it and recomputes every prefix instead of caching it, so
``render_config`` refuses an offload backend there rather than report numbers
that measured nothing. vLLM's CPU backend reads the guest's NUMA memory nodes,
so its kernel needs ``CONFIG_NUMA`` and ``CONFIG_CPUSETS``, as the
``imageless_defconfig`` preset and the default fragment set have them;
``discover`` reports whether the kernel has them and ``render_config`` refuses
a CPU engine on one without. On CPU, ``GPU Memory Utilization`` is the share of
the guest's memory the server takes for its KV cache; 0.5 leaves room in a
small guest.

The offload needs a GPU passed through, and three pieces built for it. No run
has exercised this path yet.

- the closure's ``gpu`` profile, which ships NVIDIA's driver userspace and
  builds vLLM, torch and LMCache with CUDA (:doc:`nix-build`);
- the kernel built with ``nvidia_modules``, so the driver's kernel modules
  come with the kernel the guest boots (:doc:`kernel-build`);
- the GPU picked in the boot form's Passthrough group, after the one-time
  VFIO setup in :doc:`/getting-started/requirements` (:doc:`boot`).

``discover`` takes the device to be ``cuda`` only when :cmd:`nvidia-smi`
exits successfully, and the run takes the device from there.

The run form
============

The Presets group picks which recorded sessions to replay, by the source a
session comes from (``swebench`` code repair, ``gaia`` research, ``wildclaw``
long-context tool use, or all of them), and whether the recorded pause before
each turn is kept or dropped for maximum pressure. They come from the guest's
own replayer after the first run against it.

The Engine group picks the model from a curated list of ungated chat models,
each labelled with its size and context window; a turn longer than the window
fails, and counts as a ``context`` failure rather than a crash. ``Custom
Model`` takes any other `Hugging Face`_ model id. The rest are vLLM's own
options under their own names.

The Cache group picks the offload backend: ``none`` (the default, vLLM's own
prefix cache alone and the baseline the offload is measured against), ``cpu``
memory, or ``disk`` (CPU memory and then a directory on the share, which is the
host's disk). That directory is removed before every preset and after the
run, so no preset reads a cache an earlier one wrote.

The Replay group sets how many sessions each preset replays (1 by default; a
capped replay streams only the rows it needs from the 2.4 GB dataset), how many
run at once, the cap on each turn's output length (8 tokens by default), and the
share of failed sessions a passing preset may have. Models and the dataset
download into ``/var/lib/kvcache/huggingface`` on the share, so a second run
on the same guest starts from them. Both are pinned to a Hugging Face
revision, so an A/B replays the same bytes: each curated model is served at
the revision listed in :src:`f/kvcache/common`, and the dataset is read at
``Dataset Revision``. A ``Custom Model`` is served at its latest revision.

The Advanced group is off by default. Turning on ``Override Configuration`` lets
two raw objects replace what the form renders, each only when set: ``vllm
serve`` arguments by long option name (such as ``{"tensor-parallel-size":
2}``) replace the Engine group's settings in ``vllm-serve.yaml``, and LMCache
keys (such as ``{"chunk_size": 512}``) replace the Cache group's settings in
``lmcache.yaml``. Four settings are not overridable: the server's address
(the unit pins it), the model with its served name and pinned revision (the
Engine group picks it and the replay names it), the LMCache wiring (the backend
decides it) and the ``disk`` backend's directory.

What a green run says
=====================

A preset passes when its replay finished cleanly, completed at least one
turn, and lost no more sessions than ``Max Failure Rate`` allows. The failure
rate counts only sessions that failed for a reason other than ``context``:
``(sessions_failed - failures_by_reason.context) / sessions``. A session that
outgrew the model's context window shows the trace exceeding what the model
serves, not the server failing; the report still lists it under ``failed by
reason``. On the CPU run the one ``gaia-burst`` session completed 6 of its
8 turns in 85 seconds, with 73344 of 105976 prefix cache queries hitting,
before it outgrew the 32768-token window; that run passed under a ``Max
Failure Rate`` of 1.0, and the shipped rule would pass it too by leaving the
``context`` failure out. A crashed guest, a timed-out replay or a unit that
did not finish cleanly fails the preset whatever its numbers, and so does a
replay that never ran because the server did not come up, with the server's
state as the reason; no completed turn is ``notrun``.

The verdict asks only that the numbers exist: a run with a slow time to first
token is green, and comparing runs is the reader's job.
The report shows, per preset, the time to first token and latency
percentiles, the prefix cache hit rate, and every KV cache counter the
server's ``/metrics`` moved during the replay, so a fast run that hit the
cache reads differently from one that sent short prompts. The rollup is also
written to ``/var/lib/kvcache/<kernel release>/report.json`` on the share.

Watch a replay in the job log, or follow it on the guest (see
:doc:`guests`):

.. code-block:: console
   :caption: host
   :class: cmd-host

   $ systemctl --host kvcache0 status kvcache@gaia-burst.service
   $ ssh kvcache0 journalctl --unit vllm-serve.service --follow

Running a benchmark from the CLI
================================

Run the flow with :cmd:`wmill flow run`:

.. code-block:: bash
   :class: cmd-host

   wmill flow run f/kvcache/run --data '{
     "vm_name": "kvcache0",
     "presets": ["gaia-burst"],
     "engine": {"model": "Qwen/Qwen2.5-0.5B-Instruct"},
     "cache": {"backend": "none"},
     "replay": {"sessions": 1, "max_tokens": 8}
   }'

Start one replay by hand, once a run has written the configuration onto the
share. Stopping the server first is what ``start`` does, so the replay starts
it afresh; :cmd:`systemctl` returns at once with ``--no-block``, and
:cmd:`journalctl` follows the replay:

.. code-block:: console
   :class: cmd-host

   $ systemctl --host kvcache0 stop vllm-serve.service
   $ systemctl --host kvcache0 start --no-block kvcache@gaia-burst.service
   $ ssh kvcache0 journalctl --unit kvcache@gaia-burst.service --follow

Cancelling the Windmill job runs the flow's ``stop`` failure handler, the
same step as ``teardown``, which stops the replays and the server. A
force-cancelled job skips it, so stop them by hand:

.. code-block:: console
   :class: cmd-host

   $ systemctl --host kvcache0 stop 'kvcache@*' vllm-serve.service

.. _vLLM: https://docs.vllm.ai/
.. _LMCache: https://docs.lmcache.ai/
.. _virtiofs: https://virtio-fs.gitlab.io/
.. _systemd: https://systemd.io/
.. _Hugging Face: https://huggingface.co/docs/hub/
