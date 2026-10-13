#!/usr/bin/env python3
"""Convenience entry point: `python3 main.py <command>` == `python3 bonsaiwire.py <command>`.

The whole product lives in bonsaiwire.py (single file, standard library only).
This shim exists so the tool can also be started as a plain `main.py`, and so a
packaged console script (`pip install -e .` -> `bonsaiwire`) has one obvious
counterpart in the source tree.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bonsaiwire import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
