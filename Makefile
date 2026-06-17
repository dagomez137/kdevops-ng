# SPDX-License-Identifier: copyleft-next-0.3.1
.PHONY: style generated maintainers

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
