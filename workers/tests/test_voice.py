"""One voice for Wags and the Morning Brief (prompts/wags_voice.md).

The worker's image is built from workers/ only, so workers/prompts/ carries a
copy that must be byte-identical to the repo-root original; the web side has
its own check (apps/web/e2e/wags-unit.spec.ts). If either side stops reading
the shared file, or the copy drifts, these fail.
"""

from __future__ import annotations

import unittest

from core import paths
from core.voice import load_arsenal_voice, load_voice, parse_voice


class SharedVoiceFile(unittest.TestCase):
    def test_the_image_copy_is_byte_identical_to_the_original(self) -> None:
        self.assertTrue(paths.VOICE_ORIGINAL.exists(), "prompts/wags_voice.md is the original")
        self.assertEqual(
            paths.VOICE_COPY.read_bytes(), paths.VOICE_ORIGINAL.read_bytes(),
            "workers/prompts/wags_voice.md differs: copy prompts/wags_voice.md over it",
        )

    def test_the_worker_reads_the_original_when_it_is_there(self) -> None:
        self.assertEqual(paths.VOICE_FILE, paths.VOICE_ORIGINAL)

    def test_the_parse_matches_the_web_generator(self) -> None:
        voice = load_voice()
        self.assertRegex(voice.version, r"^\d{4}-\d{2}-\d{2}\.\d+$")
        self.assertTrue(voice.text.startswith("You are Wags, Tsims's consigliere."))
        self.assertNotIn("<!--", voice.text, "the header comment is never sent to Claude")
        self.assertNotIn("VOICE_VERSION", voice.text)

    def test_a_file_without_a_version_line_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            parse_voice("<!-- x -->\nYou are Wags.")
        self.assertEqual(parse_voice("<!--\nnote\n-->\r\nVOICE_VERSION: 1\r\nHi.\r\n").text, "Hi.")


class ArsenalVoiceFile(unittest.TestCase):
    """prompts/arsenal_voice.md (Chat 5): the same original-and-copy rule."""

    def test_the_image_copy_is_byte_identical_to_the_original(self) -> None:
        self.assertTrue(paths.ARSENAL_VOICE_ORIGINAL.exists(), "prompts/arsenal_voice.md is the original")
        self.assertEqual(
            paths.ARSENAL_VOICE_COPY.read_bytes(), paths.ARSENAL_VOICE_ORIGINAL.read_bytes(),
            "workers/prompts/arsenal_voice.md differs: copy prompts/arsenal_voice.md over it",
        )

    def test_the_worker_reads_the_original_when_it_is_there(self) -> None:
        self.assertEqual(paths.ARSENAL_VOICE_FILE, paths.ARSENAL_VOICE_ORIGINAL)

    def test_it_parses_and_names_the_rooms(self) -> None:
        voice = load_arsenal_voice()
        self.assertRegex(voice.version, r"^\d{4}-\d{2}-\d{2}\.\d+$")
        self.assertTrue(voice.text.startswith("You write the Arsenal"))
        self.assertNotIn("<!--", voice.text)
        for room in ("boardroom:", "pitch:", "negotiation:", "homies:"):
            self.assertIn(room, voice.text)


if __name__ == "__main__":
    unittest.main()
