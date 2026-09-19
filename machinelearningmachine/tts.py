"""
Cross-platform text-to-speech for the CLI, using only what the OS already
ships. No extra packages, no API keys:

- macOS   -> ``say``
- Windows -> built-in PowerShell ``System.Speech`` (SAPI)
- Linux   -> ``espeak-ng`` / ``espeak`` when installed (``say`` fallback)

Used by ``module-mesh run --speak`` so terminal dialogues can be read aloud.
The web dashboard uses the browser's Web Speech API instead (see app.js).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from typing import Optional

#: Keep CLI speech payloads a reasonable size for the OS engines.
MAX_SPEAK_CHARS = 40_000


def available_engine() -> Optional[str]:
    """Return the name of the TTS engine that can be used, or None."""
    if sys.platform == "darwin":
        return "say"
    if sys.platform.startswith("win"):
        return "powershell" if shutil.which("powershell") or shutil.which("pwsh") else None
    for candidate in ("espeak-ng", "espeak"):
        if shutil.which(candidate):
            return candidate
    return None


def _powershell_exe() -> str:
    return shutil.which("powershell") or shutil.which("pwsh") or "powershell"


def speak(text: str, rate: float = 1.0) -> bool:
    """
    Read ``text`` aloud with the operating system's built-in voice.

    Returns True when an engine was found and invoked, False otherwise.
    Never raises - speech is a nice-to-have, not a hard dependency.
    """
    text = (text or "").strip()
    if not text:
        return False
    if len(text) > MAX_SPEAK_CHARS:
        text = text[:MAX_SPEAK_CHARS] + " ... [speech truncated]"

    engine = available_engine()
    if engine is None:
        print(
            "\n🔇 System text-to-speech was not found on this computer.\n"
            "   macOS: built in.\n"
            "   Windows: built in (PowerShell SAPI).\n"
            "   Linux: install with `sudo apt install espeak-ng` (or `sudo dnf install espeak-ng`)."
        )
        return False

    # Every call below passes an argv list (never a shell string) to one of the
    # fixed interpreter names returned by available_engine(); the text is an
    # argument, not a command. That is why ruff's S603/S607 are waived for this
    # file in pyproject.toml - the executable is never derived from user input.
    try:
        if engine == "say":
            # Default rate ~180 wpm; scale gently with the requested rate.
            subprocess.run(
                ["say", "-r", str(max(60, int(180 * rate))), text],
                check=False,
            )
            return True

        if engine == "powershell":
            # Write the text to a temp file: avoids all shell-quoting pain
            # with long transcripts, code samples and emoji.
            fd, path = tempfile.mkstemp(suffix=".txt", prefix="mmesh_speech_")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(text)
                ps = (
                    "Add-Type -AssemblyName System.Speech; "
                    f"$t = Get-Content -Raw -Encoding UTF8 -LiteralPath '{path}'; "
                    f"(New-Object System.Speech.Synthesis.SpeechSynthesizer).Speak($t)"
                )
                subprocess.run(
                    [_powershell_exe(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
                    check=False,
                )
            finally:
                try:
                    os.unlink(path)
                except OSError:
                    pass
            return True

        # espeak-ng / espeak
        subprocess.run(
            [engine, "-v", "en", "-s", str(max(80, int(150 * rate))), text],
            check=False,
        )
        return True
    except Exception as e:
        print(f"⚠️  Could not read aloud: {e}")
        return False
