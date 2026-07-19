# SPDX-License-Identifier: copyleft-next-0.3.1
"""Make the repo root importable so the tests import the f.* step modules."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
