"""Pre-registration stamping: section extraction, hashing, the runner guard,
and that the hashes db/017 registers still match the frozen texts on disk."""

from __future__ import annotations

import re
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from consensus import parlay_prereg
from consensus import parlay_ticket
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

    # The parlay pre-registration: v1 (owner-approved 2026-11-19, 484fd4d), §0-§10 stamped by db/085;
    # v2 (owner-approved 2026-11-20, d34a8e8) changed §0, §1, §3, §4, §7, stamped by db/086. The hashes
    # are read from the migrations, never written here.
    PARLAYS = "docs/preregistration_parlays.md"
    V2_CHANGED = ("## 0", "## 1", "## 3", "## 4", "## 7")

    def test_db085_stamps_all_eleven_v1_sections_and_db086_the_five_v2_changes(self) -> None:
        self.assertEqual([s.split(".")[0] for _, s, _ in self.rows("085")], [f"## {i}" for i in range(11)])
        self.assertEqual(tuple(s.split(".")[0] for _, s, _ in self.rows("086")), self.V2_CHANGED)
        for document, _, _ in self.rows("085") + self.rows("086"):
            self.assertEqual(document, self.PARLAYS)

    def test_every_parlay_section_on_disk_matches_its_latest_stamp(self) -> None:
        doc = (REPO_ROOT / self.PARLAYS).read_text(encoding="utf-8")
        heads = [line.rstrip() for line in doc.replace("\r\n", "\n").split("\n") if line.startswith("## ")]
        self.assertEqual(tuple(heads), parlay_prereg.SECTIONS)
        latest = {s: h for _, s, h in self.rows("085")} | {s: h for _, s, h in self.rows("086")}
        for section in heads:
            with self.subTest(section=section):
                self.assertIn(section, latest)
                self.assertEqual(pr.content_hash(pr.section_text(doc, section)), latest[section])

    def test_v2_changed_only_its_four_sections(self) -> None:
        # db/085's stamps of §0, §1, §3, §4, §7 no longer match the file (superseded by db/086, never
        # edited); its other six still do.
        doc = (REPO_ROOT / self.PARLAYS).read_text(encoding="utf-8")
        for _, section, sha in self.rows("085"):
            with self.subTest(section=section):
                if section.split(".")[0] in self.V2_CHANGED:
                    on_disk = section.replace("version P1", "version P2")
                    self.assertNotEqual(pr.content_hash(pr.section_text(doc, on_disk)), sha)
                else:
                    self.assertEqual(pr.content_hash(pr.section_text(doc, section)), sha)

    def test_an_edit_to_any_stamped_parlay_section_breaks_its_hash(self) -> None:
        doc = (REPO_ROOT / self.PARLAYS).read_text(encoding="utf-8")
        latest = {s: h for _, s, h in self.rows("085")} | {s: h for _, s, h in self.rows("086")}
        for section in parlay_prereg.SECTIONS:
            with self.subTest(section=section):
                body = pr.section_text(doc, section)
                edited = doc.replace(body, body[:-1] + ("X" if body[-1] != "X" else "Y"), 1)
                self.assertNotEqual(pr.content_hash(pr.section_text(edited, section)), latest[section])

    @staticmethod
    def rows(number: str) -> list[tuple[str, str, str]]:
        sql = next((REPO_ROOT / "db").glob(f"{number}_*.sql")).read_text(encoding="utf-8")
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


class ParlayGuard(unittest.TestCase):
    """The generator refuses to commit unless every v2 section is stamped before now (owner, Nov 20)."""

    NOW = datetime(2026, 11, 22, 16, 45, tzinfo=timezone.utc)

    def test_every_section_stamped_before_now_passes(self) -> None:
        with mock.patch.object(ledger, "preregistration_stamp", return_value=(self.NOW - timedelta(hours=1), self.NOW)):
            got = parlay_prereg.require_stamped()
        self.assertEqual([g["section"] for g in got], list(parlay_prereg.SECTIONS))

    def test_one_unstamped_section_refuses_the_whole(self) -> None:
        def stamp(*, document, section, content_sha256):
            return None if section.startswith("## 3.") else (self.NOW - timedelta(hours=1), self.NOW)

        with mock.patch.object(ledger, "preregistration_stamp", side_effect=stamp), \
                self.assertRaises(pr.PreregistrationMissing):
            parlay_prereg.require_stamped()

    def test_a_stamp_not_before_now_refuses(self) -> None:
        with mock.patch.object(ledger, "preregistration_stamp", return_value=(self.NOW, self.NOW)), \
                self.assertRaises(pr.PreregistrationMissing):
            parlay_prereg.require_stamped()

    def test_a_file_still_at_version_1_has_no_v2_section_3_and_is_refused(self) -> None:
        v1 = (REPO_ROOT / FrozenTexts.PARLAYS).read_text(encoding="utf-8").replace("version P2", "version P1")
        with mock.patch("pathlib.Path.read_text", return_value=v1), \
                mock.patch.object(ledger, "preregistration_stamp", return_value=(self.NOW - timedelta(hours=1), self.NOW)), \
                self.assertRaises(pr.PreregistrationMissing):
            parlay_prereg.require_stamped()

    def test_the_ticket_carries_the_guarded_rule_version(self) -> None:
        self.assertEqual(parlay_ticket.RULE_VERSION, parlay_prereg.RULE_VERSION)
        self.assertEqual(parlay_prereg.RULE_VERSION, "P2")


if __name__ == "__main__":
    unittest.main()
