"""Compatibility launcher for the root app.py bootstrap."""

from pathlib import Path
import sys

SRC_ROOT = str(Path(__file__).resolve().parent / "src")
if SRC_ROOT in sys.path:
    sys.path.remove(SRC_ROOT)
sys.path.insert(0, SRC_ROOT)


def main():
    from dtest.bootstrap import main as launch

    launch()


if __name__ == "__main__":
    main()
