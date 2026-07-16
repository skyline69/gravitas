# stremio:// deep links — addon install

Status: approved, ready to implement
Date: 2026-07-16

## Why

Addon sites publish `stremio://host/manifest.json` links behind an "Install"
button. Today Gravitas is not a handler for the scheme, so those links only
offer to open Stremio and a user must copy the URL into Settings by hand.

Scope is **addon install links only**. Detail and search deep links are
explicitly rejected rather than silently ignored, which keeps one obvious place
to add them later.

## Decisions taken

- Link shapes: addon install (`stremio://…/manifest.json`) only.
- Platforms: macOS (`CFBundleURLTypes`) and Linux (`x-scheme-handler/stremio`).
- A link arriving while Gravitas runs goes to the **running instance**; the
  second process forwards and exits.
- The install is **confirmed by the user**, never silent.

## Threat model

A `stremio://` link is a website asking a media centre to install an addon that
will then serve catalogs, metadata and stream URLs. Any page a user visits can
fire one. Two consequences drive the design:

1. **Never install silently.** The manifest is fetched and shown — name,
   version, host, description — and nothing is installed until the user clicks
   Install. The host is displayed because it, not the addon's self-declared
   name, is what identifies who is being trusted.
2. **The URL is untrusted input.** Parsing is total: any shape that is not an
   install link raises `UnsupportedLink`, which the UI toasts. No parse path
   reaches the network before the scheme and shape are validated.

## 1. Parsing (`application/deep_link.py`)

A pure function, in `application` because controllers need it and it must not
depend on infrastructure:

```python
def parse_deep_link(raw: str) -> str   # -> https manifest URL
```

- `stremio://ptube.ers.pw/manifest.json` → `https://ptube.ers.pw/manifest.json`
- Path is preserved, so configured addons (`stremio://host/<config>/manifest.json`)
  work.
- Scheme swap only, always to **https**. Stremio's own links are https-only, and
  a link that could downgrade to http would let a network attacker choose the
  addon. Local development addons over http are not reachable this way; that is
  a deliberate trade, not an oversight.
- Anything else — `stremio:///detail/…`, `stremio:///search?…`, another scheme,
  a non-manifest path, garbage — raises `UnsupportedLink(GravitasError)`.

## 2. Manifest behaviorHints (`domain/models.py`, `parsing.py`)

`AddonManifest` gains `behavior_hints: AddonBehaviorHints` with `adult`, `p2p`,
`configurable` and `configuration_required`.

- **`configurationRequired`** gates the install: such an addon cannot work until
  configured on its own web page, so installing it would produce a dead addon
  that silently serves nothing. The dialog offers "Configure in browser"
  (`<base_url>configure`) instead of Install.
- **`adult`** and **`p2p`** surface as badges in the dialog. They inform the
  decision at exactly the moment it is made, which needs no new settings
  surface. (A p2p addon installs fine; its torrent streams are simply filtered
  by `is_direct`, as ever.)

## 3. Delivery (`infrastructure/desktop/url_scheme.py`)

One module owns the platform specifics and exposes a single signal,
`linkReceived(str)`:

- **Single instance** — `QLocalSocket` tries to connect to a uid-scoped socket
  name. Connected → this is a second process: send the URL, exit 0. Refused →
  this is the primary: `removeServer()` (clearing a socket left by a crash),
  then listen.
- **macOS** — the OS delivers `QFileOpenEvent` to the running app for a
  registered scheme, so an event filter on the application catches it. This is
  the only path on macOS: the OS does not re-exec the binary with an argv.
- **Linux** — the browser runs `gravitas <url>`, so the URL arrives in argv and
  is forwarded over the socket by the guard above.

## 4. Install flow (`presentation/controllers/deep_link_controller.py`)

```
link → parse_deep_link → PreviewAddon (fetch_manifest, no install)
     → installRequested(name, version, host, description, badges…)
     → [user confirms] → AddonController.addAddon(url)
```

`PreviewAddon` is a use case over the existing `AddonSource.fetch_manifest`.
The preview costs no extra round-trip: `AddonClient` caches manifests for the
session, so the confirmed install reuses the fetched one.

The actual install delegates to `AddonController.addAddon`, so catalog refresh
and settings persistence keep flowing through the one path that already works.

Errors (unreachable host, bad manifest, unsupported link) surface as toasts
through the existing `errorOccurred` route.

The window raises and activates when a link arrives — a click in a browser
should surface the app.

## 5. Composition (`main.py`) and cold start

`main()` checks single-instance before building the app; a forwarding process
never constructs an engine. A link in argv at cold start is handled **after**
`bootstrap()`, because the repository must exist before an addon can be
installed into it.

## 6. OS registration

- macOS: `CFBundleURLTypes` in `packaging/gravitas.spec`'s `info_plist`, with
  `CFBundleURLSchemes: ["stremio"]`.
- Linux: `MimeType=x-scheme-handler/stremio;` and `Exec=gravitas %u` in the
  AppImage `.desktop`. AppImage registration requires desktop integration
  (`appimaged`/`Gear Lever`) or a hand-installed `.desktop`; note it in the
  file rather than pretending the AppImage self-registers.

## Testing

- `parse_deep_link`: install links (plain and configured), every rejected shape,
  scheme confusion (`stremio://` inside a path, `javascript:`, empty).
- `behaviorHints`: parsed defaults and each flag; `configurationRequired` gates.
- Delivery: a real `QLocalServer`/`QLocalSocket` round-trip offscreen; stale
  socket recovery.
- Controller: preview → confirm → install with fakes; refusal paths emit an
  error and install nothing.
- Everything offline; no network in the suite.

## Non-goals

- Detail/search/play deep links (rejected explicitly; own milestone).
- `addon_catalog` browsing (own milestone).
- Windows registration (no Windows target).
- Auto-updating an already-installed addon from a link: install is idempotent
  in `AddonRepository` (same id replaces), which is enough.
