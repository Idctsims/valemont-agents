"""The one voice Wags and the Morning Brief share (prompts/wags_voice.md).

Parsed exactly as apps/web/scripts/gen-voice.mjs parses it: the leading HTML
comment is dropped, the first line is `VOICE_VERSION: <version>`, and the rest
is the text sent to Claude verbatim. tests/test_voice.py checks both sides
read the same file and agree on the result.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from core.paths import ARSENAL_VOICE_FILE, VOICE_FILE


@dataclass(frozen=True)
class Voice:
    version: str
    text: str


def parse_voice(source: str) -> Voice:
    body = re.sub(r"^<!--[\s\S]*?-->\s*", "", source.replace("\r\n", "\n"))
    m = re.match(r"VOICE_VERSION:\s*(\S+)\s*\n", body)
    if not m:
        raise ValueError("voice file: the first line after the comment must be VOICE_VERSION: <version>")
    return Voice(version=m.group(1), text=body[m.end():].strip())


@lru_cache(maxsize=1)
def load_voice(path: Path = VOICE_FILE) -> Voice:
    return parse_voice(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def load_arsenal_voice(path: Path = ARSENAL_VOICE_FILE) -> Voice:
    """The Arsenal's voice (prompts/arsenal_voice.md; Chat 5), same format."""
    return parse_voice(path.read_text(encoding="utf-8"))
