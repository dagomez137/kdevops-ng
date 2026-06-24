# SPDX-License-Identifier: copyleft-next-0.3.1
.PHONY: style generated reflow maintainers docs serve lint format typecheck

DOCS_PORT ?= 8001

# Run before every commit (rule 5). Checks whitespace, EOF newlines, the HEAD
# commit-message trailers (Generated-by/Signed-off-by), generated-file drift, and
# Python lint and formatting (make lint).
style: generated lint
	@bash scripts/check-style.sh

# Fail if a committed generated file no longer matches its generator output.
generated:
	@bash scripts/check-generated.sh

# Lint and check formatting of all Python: the repo tooling under scripts/ and
# the hand-authored Windmill step scripts under f/. ruff is the single authority;
# its config lives in pyproject.toml.
lint:
	@ruff check scripts f
	@ruff format --check scripts f

# Apply ruff's lint fixes (import order) and formatting in place. Run after
# editing Python, then `wmill sync push` to store any f/ changes.
format:
	@ruff check --fix scripts f
	@ruff format scripts f

# Type-check with pyright (basic, f/ relaxed; see pyproject.toml). Advisory until
# a lint devshell ships pyright; the editor LSP and CI run the same config.
typecheck:
	@pyright

# Rewrap wmill description fields so wmill keeps them as clean literal blocks.
# Run after editing descriptions (then `wmill sync push` to store the rewrap).
reflow:
	@python3 scripts/reflow-descriptions.py --write

# Who to Cc for a change: make maintainers FILE=f/fstests/report.py
maintainers:
	@perl scripts/get_maintainer.pl --no-tree --no-git-fallback -f $(FILE)

# Render the documentation locally with the flake's pinned Sphinx toolchain.
docs:
	nix develop ./vendor/nixos-flake#docs --command \
		sphinx-build docs docs/_build/html
	@echo "docs ready: docs/_build/html/index.html"

# Serve the built HTML on 127.0.0.1 for viewing over an SSH tunnel:
#   ssh -L $(DOCS_PORT):127.0.0.1:$(DOCS_PORT) <host>
serve: docs
	python3 -m http.server $(DOCS_PORT) --bind 127.0.0.1 \
		--directory docs/_build/html
