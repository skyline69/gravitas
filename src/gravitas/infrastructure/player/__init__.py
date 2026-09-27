"""Media player adapters: libmpv's, and the native engine's (native_player)."""

import os
import sys
from pathlib import Path

# On Windows the native engine's module (gravitas_player.pyd) links MinGW
# DLLs -- FFmpeg, libplacebo, libass and theirs -- that
# scripts/build_native_player.py gathers into a directory beside it. Python
# loads an extension's dependencies from its own directory and from
# registered ones, never from PATH, so the directory is registered here,
# where every import of the module passes first. The handle is kept: the
# registration lasts only as long as it does.
_libs = Path(__file__).parent / "gravitas_player.libs"
if sys.platform == "win32" and _libs.is_dir():
    _dll_directory = os.add_dll_directory(str(_libs))
