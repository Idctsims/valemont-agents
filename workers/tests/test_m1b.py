"""M1b (docs/research/m1b_draft.md): the listing's window stop and the 100% sample's window."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.research import m1b


def page(*closes):
    rows = [{"ticker": f"KXMVENBASINGLEGAME-S{i}-X", "close_time": f"{c}T03:00:00Z", "volume_fp": "5.00"}
            for i, c in enumerate(closes)]
    return json.dumps({"cursor": "next", "markets": rows})


class WindowStop(unittest.TestCase):
    def test_stops_only_when_every_row_closes_before_the_window(self):
        self.assertFalse(m1b.page_all_before(page("2026-04-03", "2026-04-02")))  # the start day is in
        self.assertTrue(m1b.page_all_before(page("2026-04-02", "2026-03-01")))
        self.assertIsNone(m1b.page_all_before(json.dumps({"cursor": "", "markets": []})))

    def test_a_reserve_row_is_never_admitted_so_it_never_holds_the_crawl_open(self):
        self.assertTrue(m1b.page_all_before(page("2026-07-02", "2026-03-30")))


class Sample(unittest.TestCase):
    def test_every_window_combo_and_nothing_outside(self):
        legs = [{"event_ticker": "KXNBAPTS-26APR10AAABBB", "market_ticker": "KXNBAPTS-26APR10AAABBB-P-10", "side": "yes"},
                {"event_ticker": "KXNBAREB-26APR10AAABBB", "market_ticker": "KXNBAREB-26APR10AAABBB-Q-5", "side": "yes"}]
        rows = [{"ticker": f"T{d}", "close_time": f"{d}T03:00:00Z", "mve_selected_legs": legs}
                for d in ("2026-04-02", "2026-04-03", "2026-05-15", "2026-06-01", "2026-06-02")]
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / f"{m1b.SERIES}.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
            with mock.patch.object(m1b, "BASE", base):
                keep, counts = m1b.build_sample()
        self.assertEqual(sorted(r["ticker"] for r in keep), ["T2026-04-03", "T2026-05-15", "T2026-06-01"])
        self.assertEqual(counts["outside the window (dropped)"], 2)


if __name__ == "__main__":
    unittest.main()
