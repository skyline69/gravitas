"""PyInstaller entry point (thin wrapper so the spec has a script target)."""

from gravitas.main import main

if __name__ == "__main__":
    raise SystemExit(main())
