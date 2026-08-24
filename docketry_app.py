"""PyInstaller entry point: `docketry` as a double-clickable executable.

Run with no arguments (a double-click) it opens the demo dashboard;
with arguments it is exactly the docketry CLI.
"""
import sys

from docketry.cli import main

if __name__ == "__main__":
    if len(sys.argv) == 1:
        main(["demo"])
    else:
        main(sys.argv[1:])
