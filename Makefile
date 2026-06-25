# SPDX-License-Identifier: copyleft-next-0.3.1
.PHONY: style generated reflow maintainers docs serve lint format typecheck

DOCS_PORT ?= 8001

# kdevops-ng does its tooling in nix: each target below is a thin forwarder to a
# hermetic `nix run .#<verb>` app (defined in nix/apps), so the toolchain is the
# same on every host and in CI.

# Run before every commit (rule 5). The gate runs generated-file drift, the ruff
# lint and format check, and the whitespace/EOF/commit-trailer checks, all
# hermetically from the flake.
style:
	@nix run .#style

# Fail if a committed generated file no longer matches its generator output.
generated:
	@nix run .#generated

# Lint and check formatting of all Python (scripts/ and the f/ step scripts).
lint:
	@nix run .#lint

# Apply ruff's lint fixes (import order) and formatting in place. Run after
# editing Python, then `wmill sync push` to store any f/ changes.
format:
	@nix run .#format

# Type-check with pyright (basic, f/ relaxed; see pyproject.toml). Advisory: it
# is not part of `make style`.
typecheck:
	@nix run .#typecheck

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
