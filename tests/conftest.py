"""Path setup. The controller lives in a sibling repository by design."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTROLLER_ROOT = REPO_ROOT.parent / "asi-controller"

for path in (REPO_ROOT, CONTROLLER_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
