"""Pre-registration stamping: a frozen text's hash, timestamped by the database.

A git commit is a timestamp the committer's clock writes. `preregistrations`
(db/017) is one the database writes: document path, section, the section's
SHA-256, and `registered_at = now()`, append-only. Every holdout or
evaluation runner calls `require_registered()` before it reads any data, and
refuses unless:

1. the section, hashed exactly as it stands on disk now, matches a
   registered hash; and
2. that registration is strictly earlier than the database's now(), and so
   earlier than anything the runner is about to write.

An edit to a frozen section after registration therefore changes the hash, and
the runner refuses. That is the point: a frozen text must not move.

**Section selectors.**

- A heading line, exactly as written (`## 8. Forward-only ...`): that heading
  and everything under it, up to the next heading of the same or higher
  level.
- `BEFORE <heading>`: the whole document up to that heading, minus a trailing
  `---` rule. Used for a document whose execution log was appended after the
  run (CFB totals §9).

**Normalization before hashing:** CRLF → LF, trailing whitespace stripped
per line, leading and trailing blank lines dropped. Git's line-ending
conversion therefore cannot change a hash.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from core import ledger

__all__ = ["PreregistrationMissing", "normalize", "section_text", "content_hash", "require_registered"]


class PreregistrationMissing(RuntimeError):
    """No registration matches, or it does not predate this run. Never caught."""


def normalize(text: str) -> str:
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def _level(line: str) -> int:
    stripped = line.lstrip("#")
    return len(line) - len(stripped) if line.startswith("#") and stripped.startswith(" ") else 0


def section_text(markdown: str, section: str) -> str:
    lines = [line.rstrip() for line in markdown.replace("\r\n", "\n").split("\n")]
    if section.startswith("BEFORE "):
        heading = section[len("BEFORE "):]
        if heading not in lines:
            raise PreregistrationMissing(f"heading not found: {heading!r}")
        body = lines[:lines.index(heading)]
        while body and body[-1].strip() in ("", "---"):
            body.pop()
        return normalize("\n".join(body))
    if section not in lines:
        raise PreregistrationMissing(f"section not found: {section!r}")
    start = lines.index(section)
    level = _level(section)
    if level == 0:
        raise PreregistrationMissing(f"not a heading: {section!r}")
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if 0 < _level(lines[i]) <= level:
            end = i
            break
    body = lines[start:end]
    while body and body[-1].strip() in ("", "---"):
        body.pop()
    return normalize("\n".join(body))


def content_hash(text: str) -> str:
    return hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()


def require_registered(document: str, section: str) -> dict[str, Any]:
    """The stamp for `section` of `document` as it stands now, or raise."""
    sha = content_hash(section_text(Path(document).read_text(encoding="utf-8"), section))
    stamp = ledger.preregistration_stamp(document=document, section=section, content_sha256=sha)
    if stamp is None:
        raise PreregistrationMissing(
            f"{document} [{section}] sha256 {sha[:12]}… is not registered. Either it was "
            "never registered, or the text changed after registration. Refusing to run.")
    registered_at, db_now = stamp
    if not registered_at < db_now:
        raise PreregistrationMissing(f"registration {registered_at} does not predate now {db_now}")
    return {"document": document, "section": section, "sha256": sha,
            "registered_at": registered_at.isoformat(), "checked_at_db": db_now.isoformat()}
