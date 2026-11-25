import unittest

from core.offline_policy import require_local_http_url, require_offline_embedding_mode


class OfflinePolicyTests(unittest.TestCase):
    def test_loopback_ollama_url_is_allowed(self):
        self.assertEqual(
            require_local_http_url("http://127.0.0.1:11434/", "OLLAMA_BASE_URL"),
            "http://127.0.0.1:11434",
        )

    def test_remote_ollama_url_is_rejected_in_offline_mode(self):
        with self.assertRaisesRegex(RuntimeError, "loopback host"):
            require_local_http_url("https://example.com/api", "OLLAMA_BASE_URL")

    def test_remote_url_can_be_used_only_when_offline_mode_is_disabled(self):
        self.assertEqual(
            require_local_http_url("https://example.com/api", "OLLAMA_BASE_URL", offline_mode=False),
            "https://example.com/api",
        )

    def test_offline_mode_requires_local_embedding_files(self):
        with self.assertRaisesRegex(RuntimeError, "EMBED_LOCAL_FILES_ONLY"):
            require_offline_embedding_mode(True, False)


if __name__ == "__main__":
    unittest.main()
