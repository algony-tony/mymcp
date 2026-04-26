"""Allow `python -m mymcp` to invoke the CLI."""
import sys

from mymcp.cli import main

if __name__ == "__main__":
    sys.exit(main())
