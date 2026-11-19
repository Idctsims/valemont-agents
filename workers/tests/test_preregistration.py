"""Pre-registration stamping: section extraction, hashing, the runner guard,
and that the hashes db/017 registers still match the frozen texts on disk."""

from __future__ import annotations

import re
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from core import ledger
from core import preregistration as pr
from core.paths import REPO_ROOT
from jobs import holdout_cfb_totals, holdout_nfl_ml, holdout_nfl_props

DOC = """# Title

intro

## 1. One

alpha
### 1.1 Sub

beta

## 2. Two

gamma

---

## 9. Execution log

result
"""


class Sections(unittest.TestCase):
    def test_a_section_runs_to_the_next_heading_of_its_level(self) -> None:
        self.assertEqual(pr.section_text(DOC, "## 1. One"), "## 1. One\n\nalpha\n### 1.1 Sub\n\nbeta")
        self.assertEqual(pr.section_text(DOC, "### 1.1 Sub"), "### 1.1 Sub\n\nbeta")

    def test_before_drops_the_trailing_rule(self) -> None:
        text = pr.section_text(DOC, "BEFORE ## 9. Execution log")
        self.assertTrue(text.endswith("gamma"))
        self.assertNotIn("result", text)

    def test_line_endings_and_trailing_spaces_do_not_move_the_hash(self) -> None:
        crlf = DOC.replace("\n", "  \r\n")
        self.assertEqual(pr.content_hash(pr.section_text(crlf, "## 2. Two")),
                         pr.content_hash(pr.section_text(DOC, "## 2. Two")))

    def test_an_edit_moves_the_hash(self) -> None:
        edited = DOC.replace("gamma", "gamma!")
        self.assertNotEqual(pr.content_hash(pr.section_text(edited, "## 2. Two")),
                            pr.content_hash(pr.section_text(DOC, "## 2. Two")))

    def test_a_missing_section_is_refused(self) -> None:
        with self.assertRaises(pr.PreregistrationMissing):
            pr.section_text(DOC, "## 3. Three")


class FrozenTexts(unittest.TestCase):
    """If this fails, a registered text was edited after its stamp. Do not
    update the hash here: that edit is what the stamp exists to catch."""

    def test_db017_hashes_match_the_documents_on_disk(self) -> None:
        sql = (REPO_ROOT / "db/017_preregistrations.sql").read_text(encoding="utf-8")
        rows = re.findall(r"\(\s*'([^']+)',\s*'([^']+)',\s*'([0-9a-f]{64})'", sql)
        self.assertEqual(len(rows), 2)
        for document, section, sha in rows:
            with self.subTest(document=document):
                text = pr.section_text((REPO_ROOT / document).read_text(encoding="utf-8"), section)
                self.assertEqual(pr.content_hash(text), sha)

    def test_db085_parlay_hashes_match_the_document_on_disk(self) -> None:
        # The parlay pre-registration v1 (owner-approved 2026-11-19, 484fd4d): §0-§10, one stamp each.
        rows = self.db085_rows()
        self.assertEqual([s.split(".")[0] for _, s, _ in rows], [f"## {i}" for i in range(11)])
        for document, section, sha in rows:
            with self.subTest(section=section):
                self.assertEqual(document, "docs/preregistration_parlays.md")
                text = pr.section_text((REPO_ROOT / document).read_text(encoding="utf-8"), section)
                self.assertEqual(pr.content_hash(text), sha)

    def test_an_edit_to_any_stamped_parlay_section_breaks_its_hash(self) -> None:
        doc = (REPO_ROOT / "docs/preregistration_parlays.md").read_text(encoding="utf-8")
        for _, section, sha in self.db085_rows():
            with self.subTest(section=section):
                body = pr.section_text(doc, section)
                edited = doc.replace(body, body[:-1] + ("X" if body[-1] != "X" else "Y"), 1)
                self.assertNotEqual(pr.content_hash(pr.section_text(edited, section)), sha)

    @staticmethod
    def db085_rows() -> list[tuple[str, str, str]]:
        sql = (REPO_ROOT / "db/085_parlay_preregistration.sql").read_text(encoding="utf-8")
        return [(d, s.replace("''", "'"), h) for d, s, h in
                re.findall(r"\(\s*'([^']+)',\s*'((?:[^']|'')+)',\s*'([0-9a-f]{64})'", sql)]

    def test_every_runner_names_a_section_that_exists(self) -> None:
        for runner in (holdout_nfl_ml, holdout_nfl_props, holdout_cfb_totals):
            with self.subTest(runner=runner.__name__):
                pr.section_text((REPO_ROOT / runner.PREREG_DOC).read_text(encoding="utf-8"), runner.PREREG_SECTION)


class Guard(unittest.TestCase):
    NOW = datetime(2026, 10, 2, 12, tzinfo=timezone.utc)
    ARGS = ("docs/preregistration_cfb_totals.md", "BEFORE ## 9. Execution log")

    def stamp(self, value):
        return mock.patch.object(ledger, "preregistration_stamp", return_value=value)

    def test_unregistered_text_is_refused(self) -> None:
        with self.stamp(None), self.assertRaises(pr.PreregistrationMissing):
            pr.require_registered(*self.ARGS)

    def test_a_stamp_not_before_now_is_refused(self) -> None:
        with self.stamp((self.NOW, self.NOW)), self.assertRaises(pr.PreregistrationMissing):
            pr.require_registered(*self.ARGS)

    def test_an_earlier_stamp_passes_and_is_reported(self) -> None:
        with self.stamp((self.NOW - timedelta(days=1), self.NOW)) as stamp:
            got = pr.require_registered(*self.ARGS)
        self.assertEqual(got["sha256"], stamp.call_args.kwargs["content_sha256"])
        self.assertEqual(got["registered_at"], (self.NOW - timedelta(days=1)).isoformat())


if __name__ == "__main__":
    unittest.main()
