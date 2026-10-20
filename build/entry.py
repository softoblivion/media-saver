"""PyInstaller entry point. Kept outside the `app` package so the frozen exe starts through the same `main()`."""

import sys

from app.main import main

if __name__ == "__main__":
    sys.exit(main())
