"""``python3 -m aindex_query`` -- the same entry point as the ``aindex-query`` script."""

from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
