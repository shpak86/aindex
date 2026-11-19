#!/usr/bin/env python3
"""Build a ``.ai-index/`` for a Go project -- offline, Python 3.11+, stdlib only.

    python3 index_go_project.py ../my-go-project --clean
    python3 index_go_project.py . -o .ai-index --json

Run ``--help`` for the full option list; everything interesting lives in
:mod:`aindex.cli`.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running this file straight from a checkout, without installing.
_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from aindex.cli import main  # noqa: E402  (must follow the sys.path tweak)

if __name__ == "__main__":
    sys.exit(main())
