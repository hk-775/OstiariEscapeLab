from __future__ import annotations

import unittest

from scripts.check_release import validate_release


class ReleaseMetadataTests(unittest.TestCase):
    def test_public_beta_metadata_is_consistent(self) -> None:
        self.assertEqual([], validate_release("v0.3.0b1"))

    def test_mismatched_tag_is_rejected(self) -> None:
        errors = validate_release("v0.3.0b2")
        self.assertTrue(any("does not match" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
