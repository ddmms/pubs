"""Entrypoint redirecting to src/pubs.py.

Copyright (c) 2026, Alin M. Elena and contributors
Distributed under the terms of the BSD 3-Clause License.
"""
import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from pubs import *  # noqa: F401, F403
from pubs import main

if __name__ == "__main__":
    main()
