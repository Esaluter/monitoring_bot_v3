import unittest

from monitoring_bot_v3.auth import verify_secret_word


class AuthorizationTests(unittest.TestCase):
    def test_latin_secret_word(self):
        self.assertTrue(verify_secret_word("correct", "correct"))

    def test_cyrillic_secret_word(self):
        self.assertTrue(verify_secret_word("Секретное слово", "Секретное слово"))

    def test_mixed_unicode_secret_word(self):
        self.assertTrue(verify_secret_word("Пароль-Access №7 🔐", "Пароль-Access №7 🔐"))

    def test_wrong_secret_word(self):
        self.assertFalse(verify_secret_word("wrong", "correct"))
        self.assertFalse(verify_secret_word("секрет", "Секрет"))
        self.assertFalse(verify_secret_word(" пароль ", "пароль"))

    def test_empty_configuration_is_error(self):
        with self.assertRaises(RuntimeError):
            verify_secret_word("anything", "")


if __name__ == "__main__":
    unittest.main()

