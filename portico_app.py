"""PyInstaller entry point: `portico` as a double-clickable executable.

Run with no arguments (a double-click) it opens the demo dashboard;
with arguments it is exactly the portico CLI.
"""
import sys

from portico.cli import main

if __name__ == "__main__":
    if len(sys.argv) == 1:
        main(["demo"])
    else:
        main(sys.argv[1:])
