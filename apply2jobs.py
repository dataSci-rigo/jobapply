#!/usr/bin/env python3
"""Entrypoint shim — named so the process is identifiable in `ps aux`.

The real startup logic lives in jobbot/run.py.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "jobbot"))

from run import main

if __name__ == "__main__":
    main()
