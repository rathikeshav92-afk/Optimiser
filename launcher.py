"""Entry script for PyInstaller builds (it needs a plain script, not ``-m``)."""

import sys

from pcoptimizer.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
