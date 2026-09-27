"""Describe the `Gravitas` QML module for tooling: qml/Gravitas/gravitas.qmltypes.

Run after changing any Property, Signal or Slot a controller or model exposes
to QML:  uv run python scripts/build_qml_types.py
(`--check` exits non-zero instead of writing when the committed file is stale;
the test suite runs it that way.)

qmllint type-checks QML against .qmltypes files, and nothing generates one for
objects registered from Python at runtime. This does, from the same table the
app registers from (`presentation/qml_module.SINGLETONS`), with PySide6's own
tools: pyside6-metaobjectdump reads each class's Properties, Signals and Slots
out of the source, and pyside6-qmltyperegistrar writes the .qmltypes. Two
things are added in between. metaobjectdump does not recognise qasync's
`@asyncSlot`, so those methods are read from the AST here -- without them
every `DetailController.load(...)` would be flagged as a missing member. And
each QML name becomes its own entry, because one class can be several
singletons (three poster grids).

The file is for tooling only; the runtime never reads it.
"""

from __future__ import annotations

import argparse
import ast
import inspect
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import PySide6

from gravitas.presentation import qml_module

ROOT = Path(__file__).resolve().parent.parent
MODULE_DIR = ROOT / "src" / "gravitas" / "presentation" / "qml" / "Gravitas"
QMLTYPES = MODULE_DIR / "gravitas.qmltypes"
QMLDIR = MODULE_DIR / "qmldir"
# `depends QtQuick`: MpvVideo is a QQuickItem, and tooling resolves that
# prototype only through a module this one declares it builds on.
QMLDIR_TEXT = f"module {qml_module.IMPORT_NAME}\ntypeinfo {QMLTYPES.name}\ndepends QtQuick\n"

# The video item QML creates as `MpvVideo`. The concrete class is picked per
# platform at startup (Vulkan, Metal, OpenGL, software) and all four are a
# QQuickItem with one `handle` property. Described by hand: metaobjectdump
# cannot parse their class-level `Property("QVariant", getter, setter)`.
VIDEO_ELEMENT = "MpvVideo"
VIDEO_SOURCE = ROOT / "src" / "gravitas" / "presentation" / "video" / "mpv_sw_item.py"

# Qt's own classes the Python ones derive from (QObject, QAbstractListModel,
# QQuickItem...), so the registrar can resolve every prototype.
_FOREIGN_MODULES = ("core", "gui", "qml", "quick")

# asyncSlot argument types, spelled the way the meta-object system spells them.
_ARG_TYPES = {"str": "QString", "int": "int", "bool": "bool", "float": "double"}


def _tool(name: str) -> str:
    return str(Path(sys.executable).with_name(name))


def _dump(sources: list[Path]) -> list[dict[str, Any]]:
    """One metaobjectdump run over every file: a process per class made the
    freshness test cost seconds for no reason."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "dump.json"
        subprocess.run(
            [_tool("pyside6-metaobjectdump"), "-o", str(out), *map(str, sources)], check=True
        )
        dumped: list[dict[str, Any]] = json.loads(out.read_text())
        return dumped


def _type_name(node: ast.expr) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return _ARG_TYPES.get(node.id, f"{node.id}*" if node.id.startswith("Q") else node.id)
    raise ValueError(f"unsupported asyncSlot argument type: {ast.dump(node)}")


def _async_slots(source: Path, class_name: str) -> list[dict[str, Any]]:
    """The `@asyncSlot(...)` methods of `class_name`, as metaobjectdump would
    have described them had it known the decorator."""
    tree = ast.parse(source.read_text())
    slots: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.ClassDef) and node.name == class_name):
            continue
        for item in node.body:
            if not isinstance(item, ast.AsyncFunctionDef):
                continue
            for decorator in item.decorator_list:
                if not (
                    isinstance(decorator, ast.Call)
                    and isinstance(decorator.func, ast.Name)
                    and decorator.func.id == "asyncSlot"
                ):
                    continue
                params = [a.arg for a in item.args.args[1:]]
                types = [_type_name(arg) for arg in decorator.args]
                if len(params) != len(types):
                    raise ValueError(f"{class_name}.{item.name}: asyncSlot types do not match")
                slots.append(
                    {
                        "name": item.name,
                        "access": "public",
                        "returnType": "void",
                        "arguments": [
                            {"name": p, "type": t} for p, t in zip(params, types, strict=True)
                        ],
                    }
                )
    return slots


def _class_entries(classes: list[tuple[Path, str]]) -> dict[str, dict[str, Any]]:
    """metaobjectdump's description of each (source, class), asyncSlots added."""
    dumped = {
        entry["className"]: entry
        for file_entry in _dump(sorted({source for source, _ in classes}))
        for entry in file_entry["classes"]
    }
    entries: dict[str, dict[str, Any]] = {}
    for source, class_name in classes:
        if class_name not in dumped:
            raise LookupError(f"{class_name} not found in {source}")
        entry = dict(dumped[class_name])
        entry["slots"] = [*entry.get("slots", []), *_async_slots(source, class_name)]
        entries[class_name] = entry
    return entries


def _relative(path: Path) -> str:
    return path.resolve().relative_to(ROOT).as_posix()


def _metatypes() -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    sources = {
        cls: Path(inspect.getfile(cls)) for cls in dict.fromkeys(qml_module.SINGLETONS.values())
    }
    described = _class_entries([(source, cls.__name__) for cls, source in sources.items()])
    for name, cls in qml_module.SINGLETONS.items():
        source, entry = sources[cls], described[cls.__name__]
        entry = json.loads(json.dumps(entry))
        # One entry per QML name: the name is what QML sees, and a class that
        # is several singletons needs one export each.
        entry["className"] = name
        entry["qualifiedClassName"] = name
        entry["classInfos"] = [
            {"name": "QML.Element", "value": name},
            {"name": "QML.Singleton", "value": "true"},
            {"name": "QML.Creatable", "value": "false"},
            {"name": "QML.UncreatableReason", "value": "Built by the composition root"},
        ]
        files.append({"inputFile": _relative(source), "classes": [entry]})
    video = {
        "className": VIDEO_ELEMENT,
        "qualifiedClassName": VIDEO_ELEMENT,
        "object": True,
        "superClasses": [{"name": "QQuickItem", "access": "public"}],
        "classInfos": [{"name": "QML.Element", "value": VIDEO_ELEMENT}],
        "properties": [
            {
                "name": "handle",
                "type": "QVariant",
                "index": 0,
                "read": "handle",
                "write": "setHandle",
            }
        ],
    }
    files.append({"inputFile": _relative(VIDEO_SOURCE), "classes": [video]})
    return files


def generate() -> str:
    metatypes_dir = Path(PySide6.__file__).parent / "Qt" / "metatypes"
    foreign = [
        str(path)
        for module in _FOREIGN_MODULES
        for path in sorted(metatypes_dir.glob(f"qt6{module}_*metatypes.json"))
    ]
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        metatypes = tmp_dir / "gravitas_metatypes.json"
        metatypes.write_text(json.dumps(_metatypes(), indent=1))
        qmltypes = tmp_dir / QMLTYPES.name
        subprocess.run(
            [
                _tool("pyside6-qmltyperegistrar"),
                f"--generate-qmltypes={qmltypes}",
                "-o",
                str(tmp_dir / "registrations.cpp"),
                f"--import-name={qml_module.IMPORT_NAME}",
                f"--major-version={qml_module.MAJOR_VERSION}",
                f"--minor-version={qml_module.MINOR_VERSION}",
                f"--foreign-types={','.join(foreign)}",
                str(metatypes),
            ],
            check=True,
        )
        return qmltypes.read_text()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail if the files are stale")
    args = parser.parse_args()
    text = generate()
    if args.check:
        stale = [
            path.name
            for path, wanted in ((QMLTYPES, text), (QMLDIR, QMLDIR_TEXT))
            if not path.exists() or path.read_text() != wanted
        ]
        if stale:
            print(
                f"stale: {', '.join(stale)} -- run: uv run python scripts/build_qml_types.py",
                file=sys.stderr,
            )
            return 1
        return 0
    MODULE_DIR.mkdir(parents=True, exist_ok=True)
    QMLTYPES.write_text(text)
    QMLDIR.write_text(QMLDIR_TEXT)
    print(f"wrote {_relative(QMLTYPES)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
