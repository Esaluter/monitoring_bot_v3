from __future__ import annotations

import unittest

from monitoring_bot_v3.recipients import (
    RecipientError,
    add_recipient,
    normalize_email,
    normalize_recipients,
    remove_recipient,
)


class RecipientTests(unittest.TestCase):
    def test_normalization_and_deduplication(self):
        self.assertEqual(
            ["user@example.com"],
            normalize_recipients([" User@Example.com ", "user@example.com"]),
        )

    def test_add_and_remove(self):
        values = add_recipient(["first@example.com"], "SECOND@example.com")
        self.assertEqual(["first@example.com", "second@example.com"], values)
        self.assertEqual(["second@example.com"], remove_recipient(values, "FIRST@example.com"))

    def test_duplicate_is_rejected(self):
        with self.assertRaisesRegex(RecipientError, "уже есть"):
            add_recipient(["user@example.com"], "USER@example.com")

    def test_missing_delete_is_rejected(self):
        with self.assertRaisesRegex(RecipientError, "не найден"):
            remove_recipient([], "user@example.com")

    def test_invalid_addresses(self):
        for value in ("", "name", "a@b", "a b@example.com", "@example.com"):
            with self.subTest(value=value):
                with self.assertRaises(RecipientError):
                    normalize_email(value)


if __name__ == "__main__":
    unittest.main()

