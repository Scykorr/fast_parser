import importlib.util
from pathlib import Path

import pytest

from fast_parser import __version__
from fast_parser.cli import main

ROOT = Path(__file__).resolve().parents[1]


def _changelog_checker():
    spec = importlib.util.spec_from_file_location(
        "check_changelog",
        ROOT / ".github" / "check_changelog.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_version_is_the_package_constant():
    assert __version__ == "0.5.1"


def test_cli_prints_the_same_version(capsys):
    with pytest.raises(SystemExit) as caught:
        main(["--version"])
    assert caught.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_changelog_has_the_released_version_and_an_unreleased_section():
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## [Unreleased]" in text
    assert f"## [{__version__}] - " in text
    assert "### Добавлено" in text
    roadmap = (ROOT / "ROADMAP.md").read_text(encoding="utf-8")
    assert "0.1.0" in roadmap
    assert "сделано" in roadmap
    assert "0.2.0" in roadmap


def test_skip_changelog_label_bypasses_the_check(monkeypatch):
    monkeypatch.setenv("LABELS", "docs, skip-changelog")
    monkeypatch.delenv("BASE_SHA", raising=False)
    assert _changelog_checker().main() == 0


def test_changelog_check_needs_a_base(monkeypatch):
    monkeypatch.setenv("LABELS", "")
    monkeypatch.setenv("BASE_SHA", "")
    assert _changelog_checker().main() == 1
