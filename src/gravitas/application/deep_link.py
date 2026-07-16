"""Turn a stremio:// deep link into an addon manifest URL. Pure; no I/O.

Lives in `application` rather than `infrastructure/addons/parsing.py` because
controllers depend on it and the layer rule forbids that direction. parsing.py
maps addon JSON to models; this maps a URL to an intent.
"""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

from gravitas.domain.errors import UnsupportedLink

_SCHEME = "stremio"
_MANIFEST_SUFFIX = "/manifest.json"


def parse_deep_link(raw: str) -> str:
    """The https manifest URL a `stremio://…/manifest.json` link names.

    Raises UnsupportedLink for everything else -- other schemes, the detail and
    search deep links Gravitas does not implement, and malformed input. The
    caller toasts that message, so it has to say what was refused.

    The input is whatever a web page handed the OS, so this is a trust
    boundary: nothing here touches the network, and no string reaches the addon
    client until its scheme and shape have been checked.
    """
    link = raw.strip()
    if not link:
        raise UnsupportedLink("empty link")

    parts = urlsplit(link)
    # urlsplit lowercases nothing but the scheme; compare on that alone, so a
    # "stremio://" appearing inside some other URL's path cannot match.
    if parts.scheme.lower() != _SCHEME:
        raise UnsupportedLink(f"not a stremio:// link: {link}")

    # stremio:///detail/... and stremio:///search?... are real Stremio links
    # Gravitas has no handler for yet. They have no host, which is also what an
    # outright malformed link looks like -- so name the path either way.
    if not parts.netloc:
        raise UnsupportedLink(f"unsupported stremio link: {link}")

    if not parts.path.endswith(_MANIFEST_SUFFIX):
        raise UnsupportedLink(f"not an addon install link: {link}")

    # Scheme swap only: the host, path (a configured addon carries its settings
    # there), query and port are the addon's identity and must survive intact.
    #
    # Always https, never http: Stremio's own install links are https, and
    # honouring a downgrade would let anyone on the network pick which addon
    # gets installed. It does cost http://127.0.0.1 development addons, which
    # remain installable by pasting the URL into Settings.
    return urlunsplit(("https", parts.netloc, parts.path, parts.query, ""))
