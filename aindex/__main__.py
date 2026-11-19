"""``python3 -m aindex`` -- same CLI as ``index_go_project.py``."""

from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
