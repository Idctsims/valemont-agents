"""The parlay generator's stamp guard (pre-registration version 2; owner, 2026-11-20).

Nothing commits a parlay ticket unless every section of
docs/preregistration_parlays.md, hashed exactly as it stands, matches a stamp
the database made before now: db/085 for the seven sections version 2 left
unchanged, db/086 for §0, §3, §4 and §7. `core.preregistration` does the
hashing and the database check; this module names the sections of version 2
and refuses as a whole. Phase 4.4's adapter calls `require_stamped()` before
every slate. It raises PreregistrationMissing, which is never caught.

Version 2 is the version: the heading `## 3. Leg rules, version P2
(provisional)` must be the one on disk, and §3's rules are rule version P2.
A file still at version 1 has no such heading, and the guard refuses.

4.4 note: the Railway image is built from workers/ only, and this guard reads
docs/ from REPO_ROOT. Before the adapter runs on the worker, the text must be
in the image (a byte-identical, drift-tested copy, as prompts/wags_voice.md is).
"""

from __future__ import annotations

from typing import Any

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


def require_stamped() -> list[dict[str, Any]]:
    """Every section's stamp, or PreregistrationMissing for the first that has none."""
    return [pr.require_registered(DOCUMENT, s) for s in SECTIONS]
