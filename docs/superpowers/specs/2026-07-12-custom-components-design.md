# Gravitas — Custom Themed Component Set

**Date:** 2026-07-12
**Status:** Approved (brainstorming)

## Context

Every interactive control in the app is a stock Qt Quick Controls type
(`Button`, `TextField`, `ComboBox`, `BusyIndicator`), so it renders with the
host OS's native style — the macOS blue focus ring, the gray push button, the
blue combo chevron. This clashes with the app's dark media-center look.

This work replaces those with a small set of custom, consistently themed
components driven by a shared `Theme` singleton, with subtle animations. No
control should render a single native pixel.

## Decisions

| Topic | Decision |
|-------|----------|
| Theme source | A `pragma Singleton` `Theme.qml` in `qml/components/`, declared in a `components/qmldir`. All components + screens read tokens from it. |
| Accent | Violet `#8B5CF6`. |
| Build approach | Restyle Qt Quick Controls: each custom component subclasses the Controls type and overrides `background` / `contentItem` / `indicator` / `popup` / `delegate` with themed items. Keeps keyboard/focus/behavior; nothing native renders. |
| Components | `AppButton`, `AppTextField`, `AppComboBox`, `AppSpinner`. |
| Animations | Hover, press (scale 0.96), focus (border→accent), popup fade+scale, spinner rotation — via `Behavior` + `NumberAnimation`/`ColorAnimation`, ~120–180 ms. |
| Scope | Home, Discover, Player controls + the Main error bar colors. |

## Theme tokens

`Theme.qml` (`pragma Singleton`, `QtObject`) exposes read-only properties:

- Colors: `bg` `#141414`, `surface` `#1c1c1c`, `surfaceHover` `#2a2a2a`,
  `surfacePress` `#333333`, `border` `#333333`, `borderStrong` `#4a4a4a`,
  `accent` `#8B5CF6`, `accentHover` `#9d75f8`, `text` `#f0f0f0`,
  `textDim` `#9aa0a6`, `danger` `#902020`.
- Metrics: `radius` 8, `radiusSmall` 6, `spacing` 8, `controlHeight` 36.
- Type: `fontSmall` 13, `fontBody` 15, `fontTitle` 18.
- Motion: `durFast` 120, `durMed` 180, and an easing type constant
  (`Easing.OutCubic`) via `easing` int.

## Components

All live in `src/gravitas/presentation/qml/components/` and `import "."` to
reach `Theme`. Each keeps the public API its native counterpart exposed so
screen bindings change only the type name.

### `AppButton` (subclasses `Button`)

- Props: existing `text`, `onClicked`; plus `bool ghost: false` (ghost =
  transparent bg + border; default = filled surface).
- `background`: rounded `Rectangle`, `Theme.radius`; color transitions
  `surface → surfaceHover` (hover) → `surfacePress` (pressed) via
  `Behavior on color`; ghost variant uses transparent fill + `Theme.border`.
- `contentItem`: centered `Text`, `Theme.text`, `Theme.fontBody`.
- Press feedback: `scale: pressed ? 0.96 : 1` with `Behavior on scale`.
- Focus ring: a 1px accent border shown when `activeFocus` (keyboard).
- `implicitHeight: Theme.controlHeight`; horizontal padding from `Theme.spacing`.

### `AppTextField` (subclasses `TextField`)

- Keeps `text`, `placeholderText`.
- `background`: rounded `Rectangle`; border color `Theme.border →
  Theme.accent` when `activeFocus`, `Behavior on color` (`durFast`).
- `color: Theme.text`; `placeholderTextColor: Theme.textDim`;
  `selectionColor: Theme.accent`.
- `implicitHeight: Theme.controlHeight`; left/right padding.

### `AppComboBox` (subclasses `ComboBox`)

- Keeps `model`, `currentIndex`, `onActivated`.
- `background`: rounded `Rectangle`, `Theme.surface`, border to
  `borderStrong`/`accent` on hover/focus.
- `contentItem`: `Text` of `displayText`, `Theme.text`, elided.
- `indicator`: a custom chevron (`Canvas` or a rotated glyph `Text` "⌄")
  tinted `Theme.textDim`, no native arrow.
- `popup`: themed `Popup` — `background` a rounded `surface` `Rectangle` with
  `border`; enter transition fades opacity 0→1 and scales 0.96→1 over
  `durMed`; exit reverses.
- `delegate`: `ItemDelegate` whose `background` highlights `surfaceHover` on
  hover / `accent`-tinted when `highlighted`, `contentItem` a themed `Text`.

### `AppSpinner` (replaces `BusyIndicator`)

- A plain `Item` with `bool running`; draws a rotating 270° accent arc
  (`Canvas` or a masked `Rectangle` ring) via a `RotationAnimator` that runs
  only while `running`; hidden when not running.
- Public surface used by screens: `running` (matches how `BusyIndicator` is
  toggled).

## Application

Swap types in the screens (bindings unchanged):

- `Home.qml`: `TextField → AppTextField`, `Button → AppButton`,
  `BusyIndicator → AppSpinner`.
- `Discover.qml`: `Button → AppButton (ghost for Back)`, `ComboBox →
  AppComboBox` ×3, `BusyIndicator → AppSpinner`.
- `Player.qml`: `Button → AppButton` ×3, `ComboBox → AppComboBox`.
- `Main.qml`: error bar `Rectangle` uses `Theme.danger`/`Theme.text` (drops
  the hard-coded `#902020`). The addon-bar rectangle in `Home.qml` uses
  `Theme.surface`.

`import "components"` in screens continues to resolve the `App*` types (now
declared in `qmldir`); `Theme` is available to screens as `Theme.*` after the
`import "components"` (singleton exposed by the same qmldir).

## qmldir / singleton wiring

Create `src/gravitas/presentation/qml/components/qmldir`:

```
singleton Theme 1.0 Theme.qml
AppButton 1.0 AppButton.qml
AppTextField 1.0 AppTextField.qml
AppComboBox 1.0 AppComboBox.qml
AppSpinner 1.0 AppSpinner.qml
PosterCard 1.0 PosterCard.qml
CatalogRowStrip 1.0 CatalogRowStrip.qml
StreamRow 1.0 StreamRow.qml
```

A directory import (`import "components"`) reads this `qmldir` and supports the
`singleton` declaration. Components inside the directory reach the singleton
with `import "."` + `Theme.<token>`.

## Error handling

Purely presentational; no new error paths. Existing controller error/loading
signals and bindings are unchanged (the swapped components keep the same
properties the bindings use: `text`, `placeholderText`, `model`,
`currentIndex`, `onActivated`, `onClicked`, `running`).

## Testing

- QML has no unit tests; verified at launch. The composition test's
  `assert engine.rootObjects()` loads `Main.qml → Home → components`, so a
  parse/type error in any restyled component or the `qmldir` fails the suite
  headlessly. The existing `test_discover_qml_loads` similarly covers
  `Discover.qml` (which now instantiates `AppComboBox`/`AppButton`).
- Add a focused headless load test per new component is unnecessary — they
  are exercised transitively by the two QML-load tests once the screens use
  them. If a component is not yet referenced by a loaded screen at test time,
  it is still covered because every component is referenced by a screen in
  this change.
- Gates: `ruff check`, `ruff format --check`, `mypy --strict src`, `pytest`
  all green (this change is QML-only; no Python types change).
- Manual: launch and eyeball each screen — themed controls, hover/press/focus
  animations, dropdown open/close animation, spinner.

## Out of scope

- Restyling `PosterCard`/`CatalogRowStrip`/`StreamRow` beyond reading `Theme`
  colors where trivial (they are already custom, not native).
- New controls not currently used (checkbox, slider, scrollbar).
- Any layout/navigation change; behavior stays identical.
- The Detail sidebar enrichment (that is the separate step-3 spec).
