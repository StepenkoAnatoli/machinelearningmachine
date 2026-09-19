"""
How long does opening the *Sessions* panel take, and what did the fix buy?

Writes a directory of realistic saved transcripts (default: 50 files, ~1.5 MB each -
the size a long multi-agent code conversation actually reaches) into a temporary
directory, then times two listings of it:

* **header** - what ``sessions.list_sessions()`` does now: read the first
  ``META_HEAD_BYTES`` of each file and take ``message_count`` from there;
* **full parse** - what it used to do: read and ``json.loads`` every transcript in
  full before it could say how many messages each had.

The second number is the regression this measures, so both are printed side by side.
Nothing here touches the real ``~/.module_mesh``: the store is pointed at a temp dir
for the process, and the whole thing is deleted on exit.

    python scripts/bench_sessions.py --files 50 --messages 220

Exit status is 0 when the header path is faster than the full parse (it always should
be); 1 means the optimisation has been undone somewhere.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from machinelearningmachine import sessions as store


def _messages(count: int, size: int) -> list:
    block = "def handle(event):\n    return event\n\n"
    filler = (block * (size // len(block) + 1))[:size]
    return [
        {
            "id": f"m{i}",
            "sender_id": "copilot",
            "sender_name": "GitHub Copilot",
            "recipient_id": "*",
            "topic": "general",
            "message_type": "proposal",
            "content": filler,
            "artifacts": {},
            "metadata": {"simulated": True},
            "timestamp": 1_700_000_000 + i,
        }
        for i in range(count)
    ]


def _full_parse_listing(directory: Path) -> int:
    """The pre-fix behaviour, kept here so the comparison is not rhetorical."""
    rows = 0
    for path in sorted(directory.glob("*.json"), reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            rows += 1 + len(data.get("messages") or [])
    return rows


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--files", type=int, default=50, help="how many transcripts to create")
    parser.add_argument("--messages", type=int, default=220, help="messages per transcript")
    parser.add_argument("--chars", type=int, default=6000, help="approx characters per message")
    parser.add_argument("--keep", action="store_true", help="leave the temp directory in place")
    args = parser.parse_args(argv)

    if not (1 <= args.files <= 2000):
        print("--files must be between 1 and 2000", file=sys.stderr)
        return 2

    directory = Path(tempfile.mkdtemp(prefix="mmm-bench-"))
    # sessions_dir() re-reads the environment on every call, so this is the whole
    # isolation story: the store points at a directory this process deletes.
    os.environ["MACHINELEARNINGMACHINE_SESSIONS_DIR"] = str(directory)

    try:
        started = time.perf_counter()
        for i in range(args.files):
            store.save_session(f"bench {i:03d}", [], _messages(args.messages, args.chars))
        build = time.perf_counter() - started
        disk = sum(f.stat().st_size for f in directory.glob("*.json"))

        # Warm the page cache so neither side of the comparison wins on I/O luck.
        store.list_sessions()
        _full_parse_listing(directory)

        t0 = time.perf_counter()
        rows = len(store.list_sessions())
        header_ms = (time.perf_counter() - t0) * 1000

        t0 = time.perf_counter()
        _full_parse_listing(directory)
        parse_ms = (time.perf_counter() - t0) * 1000

        print(f"{args.files} transcripts, {disk / 1e6:.1f} MB on disk "
              f"({args.messages} messages of ~{args.chars} chars each; built in {build:.1f}s)")
        print(f"  list_sessions (header index) : {header_ms:6.1f} ms   -> {rows} rows")
        print(f"  read + parse every file     : {parse_ms:6.1f} ms")
        if parse_ms > 0 and header_ms > 0:
            print(f"  ratio                           : {parse_ms / header_ms:.1f}x")

        if header_ms >= parse_ms:
            print("\nThe header-based listing is not faster than parsing everything.",
                  "Either the optimisation has been undone or the files are too small to measure.",
                  file=sys.stderr)
            return 1
        print("\nok")
        return 0
    finally:
        if args.keep:
            print(f"(kept) {directory}")
        else:
            shutil.rmtree(directory, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
