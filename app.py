"""Root entrypoint for local/container execution and platform bootstrap wiring."""

from pathlib import Path
import sys

SRC = str(Path(__file__).resolve().parent / "src")
if SRC in sys.path:
    sys.path.remove(SRC)
sys.path.insert(0, SRC)


if __name__ == "__main__":
    from dtest.bootstrap import main

    main()
