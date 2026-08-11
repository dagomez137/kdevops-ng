# SPDX-License-Identifier: copyleft-next-0.3.1
"""Fixture tests for the git-mirror unit templates (`f.workbench.mirror`)."""

from f.workbench import mirror


def test_service_template_is_host_pinned_and_complete():
    text = mirror._SERVICE.format(git="/g", mirror_dir="/m", machine_id="abc123")
    assert "ConditionHost=abc123" in text
    assert "ExecStart=/g -C /m/%i.git remote update --prune" in text
    assert "{" not in text


def test_timer_template_is_host_pinned_and_carries_no_schedule():
    text = mirror._TIMER.format(machine_id="abc123")
    assert "ConditionHost=abc123" in text
    assert "OnBootSec" not in text
    assert "{" not in text
