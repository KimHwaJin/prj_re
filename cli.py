"""Source-checkout launcher; development CLI lives outside the service package."""
from pathlib import Path
import sys

if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parent / 'src'))
    from devtools.analysis.cli import main
    main()
