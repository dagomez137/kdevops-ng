# SPDX-License-Identifier: copyleft-next-0.3.1
"""Fixture tests for the build's check selection and verdict (`f.kernel`)."""

import json

import pytest

from f.kernel import judge_checks as judge
from f.kernel import report_checks as report
from f.kernel import select_checks


def _config(tmp_path, *symbols):
    (tmp_path / ".config").write_text("".join(f"{s}=y\n" for s in symbols))
    return str(tmp_path)


def test_default_set_on_a_rust_tree(tmp_path):
    out = select_checks.main(_config(tmp_path, "CONFIG_RUST"))
    assert out["checks"] == select_checks.DEFAULT
    assert out["skipped"] == {}
    assert out["run"]["check_clippy"] and not out["run"]["check_cocci"]


def test_rust_checks_skip_without_config_rust(tmp_path):
    out = select_checks.main(_config(tmp_path))
    assert set(out["skipped"]) == set(select_checks.RUST)
    assert not any(out["run"][select_checks.CHECKS[n]] for n in select_checks.RUST)
    assert out["run"]["check_style"]


def test_clang_analyzers_need_a_clang_build(tmp_path):
    picked = ["clang-tidy", "clang-analyzer", "checkpatch"]
    gcc = select_checks.main(_config(tmp_path), checks=picked)
    assert set(gcc["skipped"]) == {"clang-tidy", "clang-analyzer"}
    assert not gcc["run"]["check_clang"]
    clang = select_checks.main(_config(tmp_path, "CONFIG_CC_IS_CLANG"), checks=picked)
    assert clang["run"]["check_clang"]
    assert clang["clang_tools"] == ["clang-tidy", "clang-analyzer"]


def test_an_empty_pick_runs_nothing_and_unknown_names_raise(tmp_path):
    build = _config(tmp_path)
    assert select_checks.main(build, checks=[])["checks"] == []
    with pytest.raises(ValueError, match="unknown checks: smatch"):
        select_checks.main(build, checks=["smatch"])
    with pytest.raises(ValueError, match="scope"):
        select_checks.main(build, scope="file")


def _check(check, status, errors=0, warnings=0, findings=()):
    return {
        "check": check,
        "status": status,
        "errors": errors,
        "warnings": warnings,
        "checks": 0,
        "log": f"/c/{check}.log",
        "findings": list(findings),
    }


RESULTS = {
    "check_warnings": _check(
        "W=1",
        "passed",
        warnings=1,
        findings=[{"file": "lib/a.c", "line": 3, "level": "warning", "message": "w"}],
    ),
    "check_style": _check(
        "checkpatch",
        "failed",
        errors=1,
        findings=[{"file": "0001.patch", "line": 9, "level": "error", "message": "e"}],
    ),
    "check_cocci": None,
    "check_sparse": {"error": {"name": "RuntimeError", "message": "sparse rejected"}},
}


def test_report_writes_the_manifest_and_four_tables(tmp_path):
    series = {"check_dir": str(tmp_path), "base": "v7.2", "commits": 2}
    selection = {"scope": "series", "skipped": {"clippy": "CONFIG_RUST is not set"}}
    out = report.main(series, selection, RESULTS, {"entries": ["A <a@b> (maintainer)"]})
    info, rows, findings, recipients = out["render_all"]
    assert list(out) == ["render_all"]
    assert [r["check"] for r in rows] == ["W=1", "checkpatch", "check_sparse", "clippy"]
    assert [r["status"] for r in rows] == ["✅", "❌", "❌ error", "⊘"]
    assert rows[2]["note"] == "sparse rejected"
    assert [f["level"] for f in findings] == ["error", "warning"]
    assert recipients == [{"recipient": "A <a@b> (maintainer)"}]
    manifest = json.loads((tmp_path / "report.json").read_text())
    assert manifest["results"]["check_style"]["errors"] == 1
    assert info[0]["report"] == str(tmp_path / "report.json")


def test_judge_fails_on_a_failed_or_raised_check():
    with pytest.raises(RuntimeError, match=r"checkpatch \(failed\).*check_sparse"):
        judge.main(RESULTS)


def test_judge_passes_through_the_report():
    passed = {"check_warnings": RESULTS["check_warnings"], "check_cocci": None}
    assert judge.main(passed, {"render_all": []}) == {"render_all": []}


def test_checkstack_names_only_the_functions_a_series_defines(tmp_path):
    from f.kernel import check_stack

    patch = tmp_path / "0001.patch"
    patch.write_text(
        "+int xa_check_demo(struct xarray *xa, int extra)\n"
        "+\treturn xa_empty(xa) ? extra : 0;\n"
        "+static inline struct foo *bar_baz(void)\n"
        "+EXPORT_SYMBOL(xa_check_demo);\n"
        " int untouched(void)\n"
    )
    assert check_stack._defined([str(patch)]) == {"xa_check_demo", "bar_baz"}


class _Shell:
    """A devShell whose command fails before the tool runs, as nix does."""

    def __init__(self, output, rc):
        self.output, self.rc = output, rc

    def tee(self, *argv, log, cwd=None, env=None):
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(self.output)
        return self.rc

    def capture(self, *argv, cwd=None, **kwargs):
        raise RuntimeError("nix develop failed")


NIX_FAILED = "error: flake 'path:/v/nixos-flake' does not provide attribute 'x'\n"


@pytest.fixture
def sandbox(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKERS_DIR", str(tmp_path))
    monkeypatch.setenv("WORKER_INDEX", "0000")
    return tmp_path


def test_checkpatch_that_never_ran_fails(monkeypatch, sandbox):
    from f.kernel import check_style

    monkeypatch.setattr(check_style, "shell", lambda: _Shell(NIX_FAILED, 1))
    assert check_style.main("/w", ["/p/0001.patch"])["status"] == "failed"
    ok = "0001.patch:3: WARNING:X: y\ntotal: 0 errors, 1 warnings, 9 lines checked\n"
    monkeypatch.setattr(check_style, "shell", lambda: _Shell(ok, 1))
    assert check_style.main("/w", ["/p/0001.patch"])["status"] == "passed"


def test_rustfmt_that_never_ran_fails(monkeypatch, sandbox):
    from f.kernel import check_rustfmt

    monkeypatch.setattr(check_rustfmt, "shell", lambda: _Shell(NIX_FAILED, 1))
    assert check_rustfmt.main("/w", "/w/build", [])["status"] == "failed"


def test_check_uapi_that_never_ran_raises(monkeypatch, sandbox):
    from f.kernel import check_uapi

    monkeypatch.setattr(check_uapi, "shell", lambda: _Shell(NIX_FAILED, 1))
    with pytest.raises(RuntimeError, match="did not run"):
        check_uapi.main("/w", ["include/uapi/linux/fs.h"], "abc")
