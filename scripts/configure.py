#!/usr/bin/env python3
"""Initialize a private YAML profile, or explicitly import a legacy dotenv once."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from service_runtime.configuration_files import initialize_profile
from service_settings import ConfigurationError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["init", "import-env"])
    parser.add_argument("--env", choices=["local", "dev", "stg", "prd"], default="local")
    parser.add_argument("--input", type=Path, help="Required only for import-env")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--overwrite", action="store_true", help="Explicitly replace the target private YAML")
    args = parser.parse_args()
    if (args.action == "import-env") != (args.input is not None):
        parser.error("import-env requires --input; init does not read dotenv")
    try:
        target = initialize_profile(args.env, root=ROOT, source_env=args.input,
                                    output=args.output, overwrite=args.overwrite)
    except FileExistsError:
        parser.error("Target already exists; edit it or explicitly select --overwrite")
    except ConfigurationError as error:
        parser.error(str(error))
    print("Private YAML prepared: " + target.name + " (mode0600). Review values before starting.")


if __name__ == "__main__":
    main()
