"""Gravitas's own Trakt API app credentials.

Trakt has no anonymous access: every client ships an application id/secret
pair (Stremio, Kodi and friends embed theirs the same way — for an installed
app the "secret" is distribution-visible by nature, and Trakt's device flow
is designed with that in mind).

Fill these in once from https://trakt.tv/oauth/applications (any redirect
URI works; the device flow never uses it). The env vars override the
constants, so packagers and developers can inject credentials without
touching source.
"""

from __future__ import annotations

import os

_BUILT_IN_CLIENT_ID = "cnp0iad_2s0sCLmrbg8tBLfaRdNAqPED4M2BbJVcnwo"
_BUILT_IN_CLIENT_SECRET = "fRAVeC4rybqvNGDRSp6TL3fgsG4ONY1L40xnTsFXn2I"

CLIENT_ID = os.environ.get("GRAVITAS_TRAKT_CLIENT_ID", "").strip() or _BUILT_IN_CLIENT_ID
CLIENT_SECRET = (
    os.environ.get("GRAVITAS_TRAKT_CLIENT_SECRET", "").strip() or _BUILT_IN_CLIENT_SECRET
)
