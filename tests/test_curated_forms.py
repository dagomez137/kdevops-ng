# SPDX-License-Identifier: copyleft-next-0.3.1
"""Every input a flow's form shows is a curated field, never an empty JSON box.

The convention is docs/contributing/forms.rst: an object or a list of objects
whose shape the schema does not spell out is a raw config box, allowed only as an
advanced override that says so in its title or description, or as the slot for
another flow's result manifest, which bringup fills and the description names.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

FLOWS = sorted(Path(__file__).resolve().parent.parent.glob("f/**/*.flow/flow.yaml"))


def _allowed(field: dict) -> bool:
    text = f"{field.get('title', '')} {field.get('description', '')}".lower()
    return "advanced" in text or "result manifest" in text


def _open_ended(field: dict) -> bool:
    if field.get("type") == "object":
        return not field.get("properties") and "format" not in field
    items = field.get("items")
    return (
        field.get("type") == "array"
        and isinstance(items, dict)
        and items.get("type") == "object"
        and not items.get("properties")
    )


def _empty_boxes(
    properties: dict | None, path: str = "", marked: bool = False
) -> list[str]:
    """The unmarked raw boxes; a group marked advanced marks what it holds."""
    found = []
    for name, field in (properties or {}).items():
        if not isinstance(field, dict):
            continue
        where = f"{path}{name}"
        inside = marked or _allowed(field)
        if _open_ended(field) and not inside:
            found.append(where)
        found += _empty_boxes(field.get("properties"), f"{where}.", inside)
        items = field.get("items")
        if isinstance(items, dict):
            found += _empty_boxes(items.get("properties"), f"{where}[].", inside)
    return found


def test_there_are_flows_to_check():
    assert len(FLOWS) > 10


@pytest.mark.parametrize(
    "flow", FLOWS, ids=lambda p: str(p.parent.relative_to(p.parents[3]))
)
def test_a_flow_form_has_no_empty_box(flow):
    schema = yaml.safe_load(flow.read_text()).get("schema") or {}
    assert _empty_boxes(schema.get("properties")) == []


def test_an_unmarked_raw_box_is_caught():
    schema = {
        "mirrors": {"type": "array", "items": {"type": "object"}},
        "guest": {"type": "object", "title": "Advanced override"},
        "kernel": {"type": "object", "description": "The build's result manifest"},
        "group": {"type": "object", "properties": {"raw": {"type": "object"}}},
        "tuning": {
            "type": "object",
            "title": "Advanced",
            "properties": {"raw": {"type": "object"}},
        },
    }
    assert _empty_boxes(schema) == ["mirrors", "group.raw"]
