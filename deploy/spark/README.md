# deploy/spark: a bare-metal GPU worker host

Attach a unified-memory GPU machine (a DGX Spark; any Grace-class host
works the same way) to an existing kdevops-ng instance as a remote
Windmill worker, and run the KV cache workload on it directly through
`f/kvcache/run_local`. Such a machine offers no PCI function to hand to
a guest, so the VFIO/guest path does not apply; the same three systemd
units from the kvcache test suite run under the host's own
`systemd --user` manager instead, and the steps drive them with
`target: local`.

The central instance (server, PostgreSQL, UI, the `vm`-tagged workers)
stays where it is. This host serves only the `spark` and `spark-run`
tags, so it never claims guest jobs.

## One-time setup

Install Nix (multi-user) and enable the unified CLI; aarch64-linux is
fully served by the binary cache. Enable lingering so the user units
survive logout:

    sh <(curl -L https://nixos.org/nix/install) --daemon
    mkdir --parents ~/.config/nix
    echo 'experimental-features = nix-command flakes' \
        >> ~/.config/nix/nix.conf
    loginctl enable-linger "$(whoami)"

Clone this repository and build the worker binary from the deploy
flake (already multi-arch; the root flake is for the repository
tooling and is not needed here):

    nix build path:./deploy/nix#windmill \
        --out-link ~/.local/state/windmill/pkgs/windmill

## Workload out-links

The three workload units resolve their executables through out-links
under the state directory, so what runs is always a pinned store path
and swapping a pin is one `nix build`:

    nix build nixpkgs#curl --out-link ~/.local/state/kvcache/pkgs/curl
    nix build path:./vendor/nixos-flake#kvcache-bench \
        --out-link ~/.local/state/kvcache/pkgs/kvcache-bench

The `vllm-env` out-link must provide `bin/vllm` with LMCache importable
in the same environment, pinned to the same LMCache revision as the
guest runs or results are not comparable across machines. A pure
nixpkgs vLLM with CUDA on aarch64 for a Blackwell part is the clean
route when it builds; wrapping the vendor's prebuilt wheels in a
fixed-hash derivation (the `librusty_v8.nix` shape) is the sanctioned
fallback. Either way:

    nix build <your vllm env> \
        --out-link ~/.local/state/kvcache/pkgs/vllm-env

## Units

Install the workload units and the worker plumbing:

    install --mode 0644 deploy/spark/systemd/vllm-serve.service \
        deploy/spark/systemd/vllm-ready.service \
        deploy/spark/systemd/kvcache-bench@.service \
        deploy/spark/systemd/windmill-tunnel.service \
        --target-directory ~/.config/systemd/user/
    install -D --mode 0644 deploy/spark/systemd/windmill-worker-spark.conf \
        ~/.config/systemd/user/windmill-worker@0000.service.d/override.conf
    install --mode 0644 deploy/nix/systemd/windmill-worker@.service \
        --target-directory ~/.config/systemd/user/

The tunnel unit expects an SSH host alias `windmill-central` in your
own SSH configuration pointing at the central host with key
authentication; the worker reads its `DATABASE_URL` from
`~/.local/state/windmill/env/database.env`, copied from the central
host and rewritten to reach PostgreSQL through the tunnel on
loopback. The worker also needs the repository's `vendor/` tree at the
path its `VENDOR_DIR` names.

    systemctl --user daemon-reload
    systemctl --user enable --now windmill-tunnel.service
    systemctl --user enable --now windmill-worker@0000.service

The worker appears on the central UI's Workers page in group `spark`.

## Running

Deploy the workspace content from the central host as usual, then:

    wmill flow run f/kvcache/run_local --data @presets/kvcache-spark.json

State and results live under `~/.local/state/kvcache` on this host
(`%S/kvcache`, the same tree the steps' `local` target reads), keyed
by kernel release exactly as the guest layout is. On a unified-memory
host the LMCache CPU buffer shares the GPU's own DRAM, so point the
local-disk backend at an NVMe mount; that tier is the one under test.
