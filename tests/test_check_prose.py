# SPDX-License-Identifier: copyleft-next-0.3.1
"""Fixture tests for the prose gate (`scripts/check-prose.py`)."""

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check-prose.py"
spec = importlib.util.spec_from_file_location("check_prose", SCRIPT)
assert spec and spec.loader
prose = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prose)

MESSAGE = """qsu: drop the unit

The unit wrote sysfs through a shell string.

    quoted log — kept as evidence
> quoted reply — also evidence

Assisted-by: LLM
Signed-off-by: A Developer <a@example.org>
"""


def test_message_lines_drop_trailers_and_quotes():
    texts = [text for _, text in prose.message_lines(MESSAGE)]
    assert "qsu: drop the unit" in texts
    assert not any("Signed-off-by" in t or "—" in t for t in texts)


def test_a_clean_message_passes(capsys):
    assert prose.check_message("m", MESSAGE) == 0
    assert capsys.readouterr().out == ""


def test_a_dash_and_a_this_patch_opener_fail(capsys):
    bad = "x: y\n\nThis patch adds z — quickly.\n"
    assert prose.check_message("m", bad) == 1
    out = capsys.readouterr().out
    assert "'This patch' opener" in out and "en or em dash" in out


def test_a_list_in_the_body_fails():
    assert prose.check_message("m", "x: y\n\n- one\n- two\n") == 1


def test_a_warning_alone_passes(capsys):
    assert prose.check_message("m", "x: y\n\nRename it in order to match.\n") == 0
    assert "warning:" in capsys.readouterr().out


def test_docstring_lines_cover_every_docstring():
    source = 'def f():\n    """One.\n\n    Two."""\n    return 1\n'
    assert prose.docstring_lines(source) == {2, 3, 4}
