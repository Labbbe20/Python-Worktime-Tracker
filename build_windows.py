"""Build a one-file Windows executable with PyInstaller."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def main() -> int:
    if not sys.platform.startswith("win"):
        print("Windows-Exe kann nur auf Windows gebaut werden. PyInstaller unterstuetzt kein Cross-Compile.")
        return 1
    if shutil.which("pyinstaller") is None:
        print("PyInstaller ist nicht installiert. Fuer den Build einmal ausfuehren:")
        print("  py -m pip install -r requirements.txt -r requirements-build.txt")
        return 1
    command = [sys.executable, "-m", "PyInstaller", "--clean", str(ROOT / "ArbeitszeitTracker.spec")]
    subprocess.run(command, cwd=ROOT, check=True)
    print("Fertig: dist\\ArbeitszeitTracker.exe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
