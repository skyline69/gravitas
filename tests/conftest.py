import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Every store in paths.py resolves through the environment, so a test that
# leaves it alone reads and writes the developer's real settings.json, caches
# and databases -- and then passes or fails on whatever those happen to hold.
# The Sources page's "Recommended" header did exactly that: locally the marks
# were made out of a connection sample a real playback had stored, and on a
# fresh runner with nothing measured there was nothing to mark and no header.
# One throwaway root for the whole session, on both branches paths.py takes.
# Assigned rather than setdefault: an inherited value is the problem here, not
# the fallback.
_TEST_HOME = tempfile.mkdtemp(prefix="gravitas-test-home-")
if sys.platform == "win32":
    _ISOLATED = {"APPDATA": "Roaming", "LOCALAPPDATA": "Local"}
else:
    _ISOLATED = {
        "XDG_CONFIG_HOME": "config",
        "XDG_DATA_HOME": "data",
        "XDG_CACHE_HOME": "cache",
    }
for _variable, _name in _ISOLATED.items():
    _directory = os.path.join(_TEST_HOME, _name)
    os.makedirs(_directory, exist_ok=True)
    os.environ[_variable] = _directory

# Match build_app's QQuickStyle.setStyle("Basic") -- without it a test process
# takes the platform default, which on macOS is the native style. Native styles
# silently ignore background/contentItem customization and warn about it, so
# every themed App* component gets checked against a style the app never runs
# under (and test_qml_components, which fails on any warning, fails with it).
#
# The env var, not QQuickStyle.setStyle(): the style is fixed at the first
# Controls import in the process, so a call from a fixture is already too late
# and warns in turn. Qt reads this before any of that happens.
os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")

# No test reaches the real network. A test that builds the whole app gets the
# real bandwidth probe, and handed a fake stream URL (http://s/v.mkv) it
# resolved "s" and then waited out its timeout: ~2s of DNS and ~2s of
# probe per test, most of the QML suite's runtime, and a pass that depended on
# how the machine's resolver felt that day. Name lookups for anything but
# loopback fail at once instead; the code under test already treats that as
# an unreachable host. Loopback stays open -- the stream proxy tests serve on
# 127.0.0.1. Tests that exercise HTTP mock the transport (respx), which never
# resolves anything.
import ipaddress  # noqa: E402
import socket  # noqa: E402

_real_getaddrinfo = socket.getaddrinfo
_LOOPBACK_NAMES = frozenset({"localhost", "localhost.localdomain"})


def _is_loopback(host: object) -> bool:
    if host is None:
        return True  # a passive bind (host=None) is local by definition
    name = host.decode() if isinstance(host, bytes) else str(host)
    if name in _LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(name.split("%")[0]).is_loopback
    except ValueError:
        return False


def _offline_getaddrinfo(host, *args, **kwargs):  # type: ignore[no-untyped-def]
    if not _is_loopback(host):
        raise socket.gaierror(socket.EAI_NONAME, f"tests are offline: {host!r}")
    return _real_getaddrinfo(host, *args, **kwargs)


socket.getaddrinfo = _offline_getaddrinfo


# No test reaches the real desktop either. The idle inhibitor would otherwise
# ask the session bus to keep the developer's screen awake whenever a test
# built the whole app and "played" something -- a real inhibition, left to the
# desktop, from a test run. The platform backend is swapped for none at all;
# tests of the inhibitor itself hand it their own backend.
from gravitas.infrastructure.desktop import idle_inhibitor as _idle_inhibitor  # noqa: E402

_idle_inhibitor._backend_for = lambda _platform: None  # type: ignore[assignment]
