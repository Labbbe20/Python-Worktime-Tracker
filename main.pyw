"""Root launcher for ArbeitszeitTracker.

Default mode starts the background tracker. Passing ``--app`` starts only the
desktop app. This allows a bundled Windows executable to use one entry point for
both processes.
"""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def main() -> None:
    if "--app" in sys.argv:
        sys.argv.remove("--app")
        from app.main import main as app_main

        app_main()
        return

    from tracker.main import main as tracker_main

    tracker_main()


if __name__ == "__main__":
    main()
