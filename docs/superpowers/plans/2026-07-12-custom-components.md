# Custom Themed Component Set Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace every native Qt Quick Controls widget with custom, consistently themed components (driven by a `Theme` singleton) plus subtle animations, so no control renders a native OS pixel.

**Architecture:** A `pragma Singleton` `Theme.qml` holds all design tokens. Four custom components (`AppButton`, `AppTextField`, `AppComboBox`, `AppSpinner`) live in `qml/components/`, each restyling its Controls base type by overriding `background`/`contentItem`/`indicator`/`popup`/`delegate` and reading `Theme`. A `components/qmldir` declares the singleton and every component; screens swap native type names for the `App*` names, bindings unchanged.

**Tech Stack:** PySide6 Qt6 QML (Quick + Quick.Controls), `pytest` (headless QML-load coverage only — QML has no unit tests), `ruff`, `mypy --strict`, `uv`.

## Global Constraints

- QML files under `src/gravitas/presentation/qml/`; components + `qmldir` under `qml/components/`.
- **qmldir governs the whole directory:** once `components/qmldir` exists, `import "components"` (from screens) and same-directory resolution expose ONLY the types the qmldir lists. It MUST always list every component present in the dir (`PosterCard`, `CatalogRowStrip`, `StreamRow`, plus each `App*` as it is created) or those screens break. Grow the qmldir in the same task that creates each component.
- Components reach the `Theme` singleton with `import "."` then `Theme.<token>` (singletons require an explicit import even from the same dir). Screens reach it via their existing/added `import "components"`.
- No behavior/layout/navigation change: swapped components keep the exact properties the bindings use (`text`, `placeholderText`, `model`, `currentIndex`, `onActivated`, `onClicked`, `running`, `textRole`).
- Headless QML-load coverage is the only automated gate for parse/type errors: `tests/test_composition.py`'s `assert engine.rootObjects()` (loads `Main.qml → Home → components`), `test_discover_qml_loads` (loads `Discover.qml`), and a new `test_player_qml_loads` (Task 2). Every task runs the full suite; a QML error there fails these.
- Gates each commit: `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src`, `uv run pytest -q`. (This work is QML-only; no Python types change, but all gates must stay green.)
- Accent color is `#8B5CF6`. Animation durations come from `Theme.durFast` (120) / `Theme.durMed` (180).
- No Claude attribution in commit messages. All commands via `uv`.

---

### Task 1: `Theme` singleton + `qmldir` (foundation)

Create the token singleton and the qmldir, listing the singleton + the three EXISTING components. No control is restyled yet — this isolates the qmldir risk (adding a qmldir must not break the screens that already `import "components"`).

**Files:**
- Create: `src/gravitas/presentation/qml/components/Theme.qml`
- Create: `src/gravitas/presentation/qml/components/qmldir`

**Interfaces:**
- Produces: singleton `Theme` with the tokens listed below; a `qmldir` exposing `Theme` + `PosterCard` + `CatalogRowStrip` + `StreamRow`.

- [ ] **Step 1: Create `Theme.qml`**

```qml
pragma Singleton
import QtQuick

QtObject {
    // colors
    readonly property color bg: "#141414"
    readonly property color surface: "#1c1c1c"
    readonly property color surfaceHover: "#2a2a2a"
    readonly property color surfacePress: "#333333"
    readonly property color border: "#333333"
    readonly property color borderStrong: "#4a4a4a"
    readonly property color accent: "#8B5CF6"
    readonly property color accentHover: "#9d75f8"
    readonly property color text: "#f0f0f0"
    readonly property color textDim: "#9aa0a6"
    readonly property color danger: "#902020"
    // metrics
    readonly property int radius: 8
    readonly property int radiusSmall: 6
    readonly property int spacing: 8
    readonly property int controlHeight: 36
    // type
    readonly property int fontSmall: 13
    readonly property int fontBody: 15
    readonly property int fontTitle: 18
    // motion
    readonly property int durFast: 120
    readonly property int durMed: 180
}
```

- [ ] **Step 2: Create `components/qmldir`**

```
singleton Theme 1.0 Theme.qml
PosterCard 1.0 PosterCard.qml
CatalogRowStrip 1.0 CatalogRowStrip.qml
StreamRow 1.0 StreamRow.qml
```

- [ ] **Step 3: Verify the app still loads headlessly**

Run: `uv run pytest tests/test_composition.py -q`
Expected: PASS — `assert engine.rootObjects()` still loads `Main.qml → Home → CatalogRowStrip → PosterCard` now that resolution goes through the qmldir. If it fails, the qmldir is missing a type that a loaded screen references — add it.

- [ ] **Step 4: Full suite + gates**

Run: `uv run pytest -q` → all green (69).
Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy src` → pass. (ruff/mypy ignore `.qml`/`qmldir`; they only need to stay green.)

- [ ] **Step 5: Commit**

```bash
git add src/gravitas/presentation/qml/components/Theme.qml src/gravitas/presentation/qml/components/qmldir
git commit -m "feat(ui): Theme singleton + components qmldir"
```

---

### Task 2: `AppButton` + apply, and a headless Player load test

**Files:**
- Create: `src/gravitas/presentation/qml/components/AppButton.qml`
- Modify: `src/gravitas/presentation/qml/components/qmldir`
- Modify: `src/gravitas/presentation/qml/Home.qml`, `Discover.qml`, `Player.qml`
- Test: `tests/test_composition.py` (add `test_player_qml_loads`)

**Interfaces:**
- Produces: `AppButton` (subclasses `Button`) with `text`, `onClicked`, plus `bool ghost: false`.

- [ ] **Step 1: Create `AppButton.qml`**

```qml
import QtQuick
import QtQuick.Controls
import "."

Button {
    id: control
    property bool ghost: false

    implicitHeight: Theme.controlHeight
    padding: Theme.spacing * 1.5
    scale: control.pressed ? 0.96 : 1.0
    Behavior on scale { NumberAnimation { duration: Theme.durFast; easing.type: Easing.OutCubic } }

    background: Rectangle {
        radius: Theme.radius
        color: control.ghost
            ? (control.pressed ? Theme.surfacePress : control.hovered ? Theme.surfaceHover : "transparent")
            : (control.pressed ? Theme.surfacePress : control.hovered ? Theme.surfaceHover : Theme.surface)
        border.width: control.activeFocus ? 2 : (control.ghost ? 1 : 0)
        border.color: control.activeFocus ? Theme.accent : Theme.border
        Behavior on color { ColorAnimation { duration: Theme.durFast } }
    }

    contentItem: Text {
        text: control.text
        color: Theme.text
        font.pixelSize: Theme.fontBody
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }
}
```

- [ ] **Step 2: Add `AppButton` to `components/qmldir`**

Append this line:

```
AppButton 1.0 AppButton.qml
```

- [ ] **Step 3: Swap `Button → AppButton` in the screens**

`Home.qml` — the Add button (leave `id`, `text`, `onClicked`, anchors intact, just rename the type):

```qml
            AppButton {
                id: addButton
                anchors.verticalCenter: parent.verticalCenter
                text: "Add"
                onClicked: {
                    addonController.addAddon(urlField.text)
                    urlField.text = ""
                }
            }
```

`Discover.qml` — the Back button becomes a ghost `AppButton`:

```qml
        AppButton { ghost: true; text: "‹ Back"; onClicked: root.back() }
```

`Player.qml` — add `import "components"` under the existing imports, then rename the three buttons:

```qml
import QtQuick
import QtQuick.Controls
import "components"
```

```qml
        AppButton { text: "Pause"; onClicked: playerController.pause() }
        AppButton { text: "Resume"; onClicked: playerController.resume() }
```

```qml
        AppButton { text: "Back"; onClicked: player.back() }
```

(Leave the `ComboBox` in `Player.qml` untouched for now — Task 4.)

- [ ] **Step 4: Add a headless Player-load test**

`Player.qml` is only instantiated at runtime (never by an existing headless test), so add coverage. Append to `tests/test_composition.py`:

```python
def test_player_qml_loads(qapp: object) -> None:
    from pathlib import Path

    from PySide6.QtCore import QObject
    from PySide6.QtQml import QQmlComponent, QQmlEngine

    import gravitas.main as gmain

    engine = QQmlEngine()
    engine.rootContext().setContextProperty("playerController", QObject())
    qml = Path(gmain.__file__).parent / "presentation" / "qml" / "Player.qml"
    component = QQmlComponent(engine, str(qml))
    obj = component.create()
    assert obj is not None, f"Player.qml failed to load: {component.errorString()}"
```

- [ ] **Step 5: Verify loads + gates**

Run: `uv run pytest tests/test_composition.py -q`
Expected: PASS — composition (`Main→Home` with `AppButton`), `test_discover_qml_loads` (`AppButton` ghost), `test_player_qml_loads` (`AppButton` ×3). A parse/type error in `AppButton.qml` or a bad `import "."` fails here.
Run: `uv run pytest -q` → green. Gates → pass.

- [ ] **Step 6: Commit**

```bash
git add src/gravitas/presentation/qml/components/AppButton.qml \
        src/gravitas/presentation/qml/components/qmldir \
        src/gravitas/presentation/qml/Home.qml \
        src/gravitas/presentation/qml/Discover.qml \
        src/gravitas/presentation/qml/Player.qml \
        tests/test_composition.py
git commit -m "feat(ui): themed AppButton + apply across screens"
```

---

### Task 3: `AppTextField` + apply

**Files:**
- Create: `src/gravitas/presentation/qml/components/AppTextField.qml`
- Modify: `src/gravitas/presentation/qml/components/qmldir`, `Home.qml`

**Interfaces:**
- Produces: `AppTextField` (subclasses `TextField`) keeping `text`, `placeholderText`.

- [ ] **Step 1: Create `AppTextField.qml`**

```qml
import QtQuick
import QtQuick.Controls
import "."

TextField {
    id: control
    implicitHeight: Theme.controlHeight
    leftPadding: Theme.spacing * 1.5
    rightPadding: Theme.spacing * 1.5
    color: Theme.text
    placeholderTextColor: Theme.textDim
    selectionColor: Theme.accent
    selectedTextColor: Theme.text
    font.pixelSize: Theme.fontBody

    background: Rectangle {
        radius: Theme.radius
        color: Theme.surface
        border.width: 1
        border.color: control.activeFocus ? Theme.accent : Theme.border
        Behavior on border.color { ColorAnimation { duration: Theme.durFast } }
    }
}
```

- [ ] **Step 2: Add to `qmldir`**

```
AppTextField 1.0 AppTextField.qml
```

- [ ] **Step 3: Swap in `Home.qml`**

```qml
            AppTextField {
                id: urlField
                anchors.verticalCenter: parent.verticalCenter
                width: parent.width - addButton.width - parent.spacing
                placeholderText: "Addon manifest URL…"
            }
```

- [ ] **Step 4: Verify + gates**

Run: `uv run pytest tests/test_composition.py -q` → PASS (composition loads `Home` with `AppTextField`).
Run: `uv run pytest -q` → green. Gates → pass.

- [ ] **Step 5: Commit**

```bash
git add src/gravitas/presentation/qml/components/AppTextField.qml \
        src/gravitas/presentation/qml/components/qmldir \
        src/gravitas/presentation/qml/Home.qml
git commit -m "feat(ui): themed AppTextField + apply on Home"
```

---

### Task 4: `AppComboBox` + apply

**Files:**
- Create: `src/gravitas/presentation/qml/components/AppComboBox.qml`
- Modify: `src/gravitas/presentation/qml/components/qmldir`, `Discover.qml`, `Player.qml`

**Interfaces:**
- Produces: `AppComboBox` (subclasses `ComboBox`) keeping `model`, `currentIndex`, `onActivated`, `textRole`.

- [ ] **Step 1: Create `AppComboBox.qml`**

```qml
import QtQuick
import QtQuick.Controls
import "."

ComboBox {
    id: control
    implicitHeight: Theme.controlHeight
    font.pixelSize: Theme.fontBody

    background: Rectangle {
        radius: Theme.radius
        color: control.pressed ? Theme.surfacePress : Theme.surface
        border.width: 1
        border.color: (control.activeFocus || control.hovered) ? Theme.borderStrong : Theme.border
        Behavior on color { ColorAnimation { duration: Theme.durFast } }
        Behavior on border.color { ColorAnimation { duration: Theme.durFast } }
    }

    contentItem: Text {
        leftPadding: Theme.spacing * 1.5
        rightPadding: control.indicator.width + Theme.spacing
        text: control.displayText
        color: Theme.text
        font: control.font
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }

    indicator: Text {
        x: control.width - width - Theme.spacing
        y: control.topPadding + (control.availableHeight - height) / 2
        text: "⌄"
        color: Theme.textDim
        font.pixelSize: Theme.fontTitle
        rotation: control.popup.visible ? 180 : 0
        Behavior on rotation { NumberAnimation { duration: Theme.durFast } }
    }

    delegate: ItemDelegate {
        width: ListView.view ? ListView.view.width : control.width
        height: Theme.controlHeight
        highlighted: control.highlightedIndex === index
        background: Rectangle {
            color: highlighted ? Theme.surfaceHover : "transparent"
        }
        contentItem: Text {
            leftPadding: Theme.spacing * 1.5
            text: control.textRole.length ? (model[control.textRole] || "")
                                          : (modelData !== undefined ? modelData : "")
            color: Theme.text
            font: control.font
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
    }

    popup: Popup {
        y: control.height + 4
        width: control.width
        implicitHeight: Math.min(contentItem.implicitHeight, 280)
        padding: 1

        background: Rectangle {
            radius: Theme.radiusSmall
            color: Theme.surface
            border.width: 1
            border.color: Theme.border
        }

        contentItem: ListView {
            clip: true
            implicitHeight: contentHeight
            model: control.popup.visible ? control.delegateModel : null
            currentIndex: control.highlightedIndex
            ScrollIndicator.vertical: ScrollIndicator {}
        }

        enter: Transition {
            NumberAnimation { property: "opacity"; from: 0.0; to: 1.0; duration: Theme.durMed; easing.type: Easing.OutCubic }
            NumberAnimation { property: "scale"; from: 0.96; to: 1.0; duration: Theme.durMed; easing.type: Easing.OutCubic }
        }
        exit: Transition {
            NumberAnimation { property: "opacity"; from: 1.0; to: 0.0; duration: Theme.durFast }
        }
    }
}
```

- [ ] **Step 2: Add to `qmldir`**

```
AppComboBox 1.0 AppComboBox.qml
```

- [ ] **Step 3: Swap in `Discover.qml`** — rename the three `ComboBox` blocks to `AppComboBox` (properties unchanged). Example for the first; do all three:

```qml
        AppComboBox {
            id: typeBox
            width: 160
            model: discoverController.typeOptions
            currentIndex: discoverController.typeIndex
            onActivated: (index) => discoverController.selectType(index)
        }
```

(Repeat for `catalogBox`/`catalogOptions`/`catalogIndex`/`selectCatalog` and `genreBox`/`genreOptions`/`genreIndex`/`selectGenre`.)

- [ ] **Step 4: Swap in `Player.qml`** — the subtitle combo (keeps `textRole`, `model`, `onActivated`):

```qml
        AppComboBox {
            id: subs
            textRole: "title"
            model: []
            onActivated: playerController.selectSubtitle(model[currentIndex].id)
        }
```

- [ ] **Step 5: Verify loads + gates**

Run: `uv run pytest tests/test_composition.py -q`
Expected: PASS — `test_discover_qml_loads` loads three `AppComboBox`es (against stub context props; binding warnings are fine, the component must parse/create) and `test_player_qml_loads` loads the subtitle `AppComboBox`.
Run: `uv run pytest -q` → green. Gates → pass.

- [ ] **Step 6: Commit**

```bash
git add src/gravitas/presentation/qml/components/AppComboBox.qml \
        src/gravitas/presentation/qml/components/qmldir \
        src/gravitas/presentation/qml/Discover.qml \
        src/gravitas/presentation/qml/Player.qml
git commit -m "feat(ui): themed AppComboBox with animated popup + apply"
```

---

### Task 5: `AppSpinner` + apply

**Files:**
- Create: `src/gravitas/presentation/qml/components/AppSpinner.qml`
- Modify: `src/gravitas/presentation/qml/components/qmldir`, `Home.qml`, `Discover.qml`

**Interfaces:**
- Produces: `AppSpinner` (an `Item`) with `bool running` (matches how `BusyIndicator.running` was toggled).

- [ ] **Step 1: Create `AppSpinner.qml`**

```qml
import QtQuick
import "."

Item {
    id: root
    property bool running: false
    implicitWidth: 36
    implicitHeight: 36
    visible: opacity > 0
    opacity: running ? 1.0 : 0.0
    Behavior on opacity { NumberAnimation { duration: Theme.durFast } }

    Canvas {
        id: canvas
        anchors.fill: parent
        onPaint: {
            const ctx = getContext("2d")
            ctx.reset()
            const cx = width / 2
            const cy = height / 2
            const r = Math.min(width, height) / 2 - 3
            ctx.lineWidth = 3
            ctx.lineCap = "round"
            ctx.strokeStyle = Theme.accent
            ctx.beginPath()
            ctx.arc(cx, cy, r, 0, Math.PI * 1.5)
            ctx.stroke()
        }
        RotationAnimator {
            target: canvas
            from: 0
            to: 360
            duration: 900
            loops: Animation.Infinite
            running: root.running
        }
    }
}
```

- [ ] **Step 2: Add to `qmldir`**

```
AppSpinner 1.0 AppSpinner.qml
```

- [ ] **Step 3: Swap `BusyIndicator → AppSpinner` in `Home.qml`**

```qml
    AppSpinner {
        id: busy
        anchors.centerIn: parent
        running: false
        Connections {
            target: catalogController
            function onLoadingChanged(loading) { busy.running = loading }
        }
    }
```

- [ ] **Step 4: Swap `BusyIndicator → AppSpinner` in `Discover.qml`**

```qml
    AppSpinner {
        id: busy
        anchors.centerIn: parent
        running: false
        Connections {
            target: discoverController
            function onLoadingChanged(loading) { busy.running = loading }
        }
    }
```

- [ ] **Step 5: Verify + gates**

Run: `uv run pytest tests/test_composition.py -q` → PASS (composition loads `Home` with `AppSpinner`; `test_discover_qml_loads` loads `AppSpinner`).
Run: `uv run pytest -q` → green. Gates → pass.

- [ ] **Step 6: Commit**

```bash
git add src/gravitas/presentation/qml/components/AppSpinner.qml \
        src/gravitas/presentation/qml/components/qmldir \
        src/gravitas/presentation/qml/Home.qml \
        src/gravitas/presentation/qml/Discover.qml
git commit -m "feat(ui): themed AppSpinner + replace BusyIndicator"
```

---

### Task 6: Theme colors on remaining chrome + final launch verification

Fold the leftover hard-coded colors into `Theme`, and eyeball every screen for the themed look + animations.

**Files:**
- Modify: `src/gravitas/presentation/qml/Main.qml`, `Home.qml`, `components/StreamRow.qml`, `Detail.qml`

**Interfaces:** none new (color-token pass only).

- [ ] **Step 1: `Main.qml` error bar → Theme**

Add `import "components"` under the existing imports, then change the error bar rectangle + label colors:

```qml
    Rectangle {
        id: errorBar
        function show(msg) { label.text = msg; visible = true; hideTimer.restart() }
        visible: false
        anchors.top: parent.top; anchors.left: parent.left; anchors.right: parent.right
        height: 40; color: Theme.danger; z: 100
        Text { id: label; anchors.centerIn: parent; color: Theme.text }
        Timer { id: hideTimer; interval: 4000; onTriggered: errorBar.visible = false }
    }
```

Also set the window background: `color: Theme.bg` (replacing `"#141414"`).

- [ ] **Step 2: `Home.qml` addon bar → Theme**

Change the `addonBar` rectangle color (Home already imports components):

```qml
        color: Theme.surface
```

- [ ] **Step 3: `StreamRow.qml` → Theme**

`StreamRow.qml` currently only `import QtQuick`. Add `import "."`, then swap the hard-coded colors:

```qml
import QtQuick
import "."

Rectangle {
    id: root
    property string name
    property string subtitle
    signal clicked()
    height: 56; radius: Theme.radiusSmall
    color: mouse.containsMouse ? Theme.surfaceHover : Theme.surface
    Behavior on color { ColorAnimation { duration: Theme.durFast } }

    Column {
        anchors.verticalCenter: parent.verticalCenter
        anchors.left: parent.left; anchors.leftMargin: 12
        Text { text: root.name; color: Theme.text; font.bold: true }
        Text { text: root.subtitle; color: Theme.textDim; font.pixelSize: Theme.fontSmall }
    }
    MouseArea { id: mouse; anchors.fill: parent; hoverEnabled: true; onClicked: root.clicked() }
}
```

- [ ] **Step 4: `Detail.qml` text colors → Theme**

`Detail.qml` already imports components. Swap the hard-coded `"white"`/`"#ccc"` in the title/description/"Sources" `Text` items to `Theme.text` / `Theme.textDim` / `Theme.text` respectively (leave sizes/bold as-is).

- [ ] **Step 5: Verify loads + gates**

Run: `uv run pytest -q`
Expected: all green — composition loads `Main` (error bar `Theme.danger`) + `Home`; `test_discover_qml_loads`; `test_player_qml_loads`. `StreamRow`/`Detail` are loaded transitively when `Detail` is (it's not in a headless test, but `StreamRow`/`Detail` parse is low-risk color-only; still, `import "."` in `StreamRow` must resolve — confirmed by the composition test only if a loaded screen uses it, which it does not at load time). Rely on the launch check in Step 6 for `Detail`/`StreamRow`.
Run gates → pass.

- [ ] **Step 6: Launch and eyeball every screen**

Run: `uv run gravitas`
Expected (needs a display): 
- **Home** — dark themed addon field with a violet focus border (not the macOS blue ring), a filled themed "Add" button (hover lightens, press shrinks slightly), themed spinner while loading.
- **Discover** (click See All) — ghost "‹ Back" button, three themed dropdowns with a violet-tinted border on hover and a custom chevron; opening one fades+scales the popup in, items highlight on hover; themed spinner.
- **Detail** (click a poster) — themed title/description, themed stream rows (hover lightens).
- **Player** — themed Pause/Resume/Back buttons + themed subtitle dropdown.
- No native macOS control anywhere. Close the app.

- [ ] **Step 7: Commit**

```bash
git add src/gravitas/presentation/qml/Main.qml \
        src/gravitas/presentation/qml/Home.qml \
        src/gravitas/presentation/qml/components/StreamRow.qml \
        src/gravitas/presentation/qml/Detail.qml
git commit -m "feat(ui): apply Theme colors to remaining chrome"
```

---

## Self-Review

**Spec coverage:**
- `Theme` singleton + qmldir wiring → Task 1. ✓
- `AppButton` (filled + ghost, hover/press/focus anims) + apply Home/Discover/Player → Task 2. ✓
- `AppTextField` (focus→accent border anim) + apply Home → Task 3. ✓
- `AppComboBox` (custom bg/chevron/popup/delegate, fade+scale popup) + apply Discover ×3/Player → Task 4. ✓
- `AppSpinner` (rotating accent arc) + replace BusyIndicator Home/Discover → Task 5. ✓
- Error bar + addon bar + StreamRow/Detail Theme colors → Task 6. ✓
- Headless load coverage for all three screens (Main/Home, Discover, Player) → Task 2 adds `test_player_qml_loads`; existing tests cover the rest. ✓
- Out of scope (new controls, layout/nav changes, Detail sidebar enrichment) — no task introduces them. ✓

**Placeholder scan:** No TBD/TODO. Every component and swap shows complete code. The one caveat noted (StreamRow/Detail `import "."`/color parse verified at launch, not headlessly) is an explicit coverage statement, not missing content.

**Type/wiring consistency:** `Theme` tokens referenced (`surface`/`surfaceHover`/`surfacePress`/`border`/`borderStrong`/`accent`/`text`/`textDim`/`danger`/`radius`/`radiusSmall`/`spacing`/`controlHeight`/`fontBody`/`fontSmall`/`fontTitle`/`durFast`/`durMed`) all exist in the Task 1 `Theme.qml`. `qmldir` grows by exactly one line per component task and always lists the three pre-existing components (Task 1). Screen swaps preserve every binding property (`text`/`placeholderText`/`model`/`currentIndex`/`onActivated`/`onClicked`/`running`/`textRole`). `import "."` used inside components (AppButton/AppTextField/AppComboBox/AppSpinner/StreamRow) for `Theme`; `import "components"` added to `Player.qml` (Task 2) and `Main.qml` (Task 6); `Home.qml`/`Discover.qml`/`Detail.qml` already import components.
