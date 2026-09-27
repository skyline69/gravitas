"""QML is type-checked, and stays that way.

qmllint checks every QML file against the Gravitas module's .qmltypes, so a
misspelled controller method, a property read that does not exist or a
delegate reading a role it never declared is a test failure here instead of
a TypeError on whichever screen happens to run it.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import PySide6
import pytest
from pytest import MonkeyPatch

from gravitas.main import _QML_DIR, build_app
from gravitas.presentation import qml_module

_ROOT = Path(__file__).resolve().parents[2]
_QMLLINT = Path(PySide6.__file__).parent / ("qmllint.exe" if sys.platform == "win32" else "qmllint")


def _all_categories() -> list[str]:
    """Every warning category this qmllint knows, raised to warning -- the
    ones off by default included, so a new kind of finding is not silently
    an info line."""
    help_text = subprocess.run(
        [str(_QMLLINT), "--help"], capture_output=True, text=True, check=False
    ).stdout
    return [f"--{name}" for name in re.findall(r"^\s+--([a-z-]+) <level>", help_text, re.M)]


@pytest.mark.skipif(not _QMLLINT.exists(), reason="this PySide6 wheel ships no qmllint")
def test_every_qml_file_lints_clean(tmp_path: Path) -> None:
    files = sorted(str(path) for path in _QML_DIR.rglob("*.qml"))
    report = tmp_path / "lint.json"
    levels = [arg for category in _all_categories() for arg in (category, "warning")]
    subprocess.run(
        [str(_QMLLINT), "-I", str(_QML_DIR), *levels, "--json", str(report), *files],
        capture_output=True,
        check=False,
    )
    findings = [
        f"{Path(entry['filename']).relative_to(_QML_DIR)}:{warning.get('line')}: "
        f"[{warning.get('id')}] {warning['message']}"
        for entry in json.loads(report.read_text())["files"]
        for warning in entry.get("warnings", [])
    ]
    assert findings == []


def test_the_qmltypes_describe_the_current_python() -> None:
    # The .qmltypes is generated from the controllers and models; stale, it
    # would let qmllint pass QML against an API that no longer exists.
    result = subprocess.run(
        [sys.executable, str(_ROOT / "scripts" / "build_qml_types.py"), "--check"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_the_app_binds_every_name_the_module_declares(
    qapp: object, tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    # The .qmltypes promises QML each of these names; a name the composition
    # root forgot would lint clean and fail at runtime.
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    _app, engine = build_app(
        argv=[], default_addon_url="https://v3-cinemeta.strem.io/manifest.json"
    )
    bound = qml_module.bound(engine)
    assert sorted(bound) == sorted(qml_module.SINGLETONS)
    for name, instance in bound.items():
        assert isinstance(instance, qml_module.SINGLETONS[name]), name
