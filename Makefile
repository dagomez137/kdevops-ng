# SPDX-License-Identifier: copyleft-next-0.3.1
.PHONY: style generated maintainers docs serve

DOCS_PORT ?= 8001

# Run before every commit (rule 5). Checks whitespace, EOF newlines, the HEAD
# commit-message trailers (Generated-by/Signed-off-by), and generated-file drift.
style: generated
	@bash scripts/check-style.sh

# Fail if a committed generated file no longer matches its generator output.
generated:
	@bash scripts/check-generated.sh

# Who to Cc for a change: make maintainers FILE=f/fstests/report.py
maintainers:
	@perl scripts/get_maintainer.pl --no-tree --no-git-fallback -f $(FILE)

# Render the documentation locally with the flake's pinned Sphinx toolchain.
docs:
	nix develop ./workers/shared/nixos-flake#docs --command \
		sphinx-build docs docs/_build/html
	@echo "docs ready: docs/_build/html/index.html"

# Serve the built HTML on 127.0.0.1 for viewing over an SSH tunnel:
#   ssh -L $(DOCS_PORT):127.0.0.1:$(DOCS_PORT) <host>
serve: docs
	python3 -m http.server $(DOCS_PORT) --bind 127.0.0.1 \
		--directory docs/_build/html
