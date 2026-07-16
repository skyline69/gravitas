import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

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
