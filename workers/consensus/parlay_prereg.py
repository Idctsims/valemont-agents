"""The parlay generator's stamp guard (pre-registration version 2; owner, 2026-11-20).

Nothing commits a parlay ticket unless every section of
docs/preregistration_parlays.md, hashed exactly as it stands, matches a stamp
the database made before now: db/085 for the six sections version 2 left
unchanged, db/086 for §0, §1, §3, §4 and §7. This module names the sections
of version 2 and refuses as a whole. The adapter calls `require_stamped()`
before every slate. It raises PreregistrationMissing, which is never caught.

Version 2 is the version: the heading `## 3. Leg rules, version P2
(provisional)` must be the one in the text, and §3's rules are rule version P2.
A text still at version 1 has no such heading, and the guard refuses.

**Where the text is read (Phase 4.4).** The Railway image is built from
`workers/` only, so `workers/prereg/preregistration_parlays.md` is a
byte-identical copy that ships in it (`core.paths.PARLAYS_PREREG_FILE`: the
repo-root original when present, else the copy). The guard refuses when:
- any section is unstamped, or stamped no earlier than now;
- the original and the copy both exist and differ (drift).
`tests/test_parlay_prereg.py` fails on drift too, so it is caught before a
deploy as well as at a commit. The stamped `document` key stays
`docs/preregistration_parlays.md` whichever file is read.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core import ledger
from core import paths
from core import preregistration as pr

DOCUMENT = "docs/preregistration_parlays.md"
RULE_VERSION = "P2"
#: Version 2's sections, by heading as it stands (§3's names the rule version).
SECTIONS: tuple[str, ...] = (
    "## 0. Amendments",
    "## 1. Agents and money",
    "## 2. Schedule and volume",
    "## 3. Leg rules, version P2 (provisional)",
    "## 4. Prices recorded on every ticket",
    "## 5. The commitment",
    "## 6. The CLV proxy",
    "## 7. The switch to M5's rules",
    "## 8. Reporting",
    "## 9. What these tickets cannot claim",
    "## 10. Data rules",
)


def text(original: Path = paths.PARLAYS_PREREG_ORIGINAL, copy: Path = paths.PARLAYS_PREREG_COPY) -> str:
    """The pre-registration's text: the original when present (and equal to the
    copy, when both exist), else the image copy. Drift refuses."""
    if original.exists() and copy.exists():
        a, b = original.read_text(encoding="utf-8"), copy.read_text(encoding="utf-8")
        if pr.normalize(a) != pr.normalize(b):
            raise pr.PreregistrationMissing(
                f"{copy} has drifted from {original}: copy the stamped original over it. Refusing to commit.")
        return a
    for path in (original, copy):
        if path.exists():
            return path.read_text(encoding="utf-8")
    raise pr.PreregistrationMissing(f"neither {original} nor {copy} exists. Refusing to commit.")


def require_stamped(*, conn: Any = None) -> list[dict[str, Any]]:
    """Every section's stamp, or PreregistrationMissing for the first that has
    none (or whose stamp does not predate now). `conn`: a read-only caller's."""
    doc = text()
    out = []
    for s in SECTIONS:
        sha = pr.content_hash(pr.section_text(doc, s))
        stamp = ledger.preregistration_stamp(document=DOCUMENT, section=s, content_sha256=sha, conn=conn)
        if stamp is None:
            raise pr.PreregistrationMissing(
                f"{DOCUMENT} [{s}] sha256 {sha[:12]}... is not stamped (never stamped, or the text changed "
                "after its stamp). Refusing to commit.")
        registered_at, db_now = stamp
        if not registered_at < db_now:
            raise pr.PreregistrationMissing(f"{DOCUMENT} [{s}] stamp {registered_at} does not predate now {db_now}")
        out.append({"section": s, "sha256": sha, "registered_at": registered_at.isoformat(),
                    "checked_at_db": db_now.isoformat()})
    return out
