#!/usr/bin/env python3
"""
Print a free local TCP port - the helper the one-click launchers call.

Why this exists: the launchers start the dashboard on ``127.0.0.1:8000``. If
anything already holds 8000 - a copy of this app left running from earlier, a
different service - the server exits immediately and a beginner is left looking
at a console window that closed itself. Instead, the launchers ask this script
for the first free port in a small range and open the browser there.

Output is a bare number on stdout and nothing else: ``launch-windows.bat`` reads
it with ``set /p``, and the shell launchers with ``$(...)``.

Run it by hand:

    python scripts/pick_port.py            # 8000, or the next free port
    python scripts/pick_port.py --start 9000
"""

from __future__ import annotations

import argparse
import socket
import sys

#: First port tried, matching the launchers' documented default.
DEFAULT_START = 8000
#: How many consecutive ports to try before letting the OS assign one.
DEFAULT_TRIES = 11
#: Ports below this need administrator rights on Linux/macOS; never suggest one.
MIN_PORT = 1024


def find_free_port(start: int = DEFAULT_START, tries: int = DEFAULT_TRIES, host: str = "127.0.0.1") -> int:
    """
    Return a port that can be bound on ``host`` right now.

    Tries ``tries`` consecutive ports from ``start``; if all of them are taken,
    asks the operating system for any free port (bind to port 0) so that the app
    still starts instead of failing over a cosmetic detail.
    """
    if tries < 1:
        raise ValueError("tries must be at least 1")
    for port in range(start, start + tries):
        if not MIN_PORT <= port <= 65535:
            continue
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind((host, port))
            except OSError:
                continue
        return port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((host, 0))
        return int(probe.getsockname()[1])


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Print a free local TCP port (used by the launchers).")
    parser.add_argument("--start", type=int, default=DEFAULT_START, help=f"first port to try (default: {DEFAULT_START})")
    parser.add_argument("--tries", type=int, default=DEFAULT_TRIES, help=f"how many ports to try (default: {DEFAULT_TRIES})")
    args = parser.parse_args(argv)
    try:
        port = find_free_port(args.start, args.tries)
    except (OSError, ValueError) as exc:  # a machine with no usable loopback, or a bad --tries
        print(f"could not find a free port: {exc}", file=sys.stderr)
        return 1
    print(port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
