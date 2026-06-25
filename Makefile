# SPDX-License-Identifier: copyleft-next-0.3.1
.PHONY: style check generated lint format typecheck fmt reflow maintainers docs serve

DOCS_PORT ?= 8001
# The system whose flake checks `make lint` and `make generated` build. The
# tooling flake only provides x86_64-linux (see flake.nix).
NIX_SYSTEM ?= x86_64-linux

# kdevops-ng does its tooling in nix, and each target uses the nix command that
# fits the task: read-only verification runs as flake checks (`nix flake check`),
# advisory and git-aware tools run from the checks devShell (`nix develop -c`),
# and programs that mutate, serve, or query run as apps (`nix run`).

# Run before every commit (rule 5). The full gate: the flake checks (ruff lint
# and format, generated-file drift, tree formatting) plus the git-aware
# whitespace, end-of-file, and commit-trailer checks that need the git repo.
style: check
	@nix develop .#checks --command bash scripts/check-style.sh

# The CI gate: every read-only verification the flake defines.
check:
	@nix flake check

# Individual flake checks (the gate runs both together via `make check`).
lint:
	@nix build --print-build-logs .#checks.$(NIX_SYSTEM).lint && echo "lint: OK"

generated:
	@nix build --print-build-logs .#checks.$(NIX_SYSTEM).generated && echo "generated: OK"

# Format the whole tree in place: nixfmt for Nix and ruff for Python, via treefmt.
fmt:
	@nix fmt

# Apply ruff's lint fixes (import order) and formatting to Python in place. Run
# after editing Python, then `wmill sync push` to store any f/ changes.
format:
	@nix run .#format

# Type-check with pyright (advisory; not part of the gate, see pyproject.toml).
typecheck:
	@nix develop .#checks --command pyright

# Rewrap wmill description fields so wmill keeps them as clean literal blocks.
# Run after editing descriptions (then `wmill sync push` to store the rewrap).
reflow:
	@nix run .#reflow

# Who to Cc for a change: make maintainers FILE=f/fstests/report.py
maintainers:
	@nix run .#maintainers -- $(FILE)

# Render the documentation locally with the flake's pinned Sphinx toolchain.
docs:
	@nix run .#docs

# Serve the built HTML on 127.0.0.1 for viewing over an SSH tunnel:
#   ssh -L $(DOCS_PORT):127.0.0.1:$(DOCS_PORT) <host>
serve: docs
	@nix run .#serve -- $(DOCS_PORT)
