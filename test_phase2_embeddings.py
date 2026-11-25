import unittest

from config import EMBED_DIM
from core.phase2_embeddings import _require_nonzero_embedding, get_local_embedding


class LocalEmbeddingAdapterTests(unittest.TestCase):
    def test_local_embedding_uses_normalized_sentence_transformer_encoding(self):
        calls = []

        class FakeEmbedding:
            def tolist(self):
                return [0.25] * EMBED_DIM

        class FakeEmbedder:
            def encode(self, text, **kwargs):
                calls.append((text, kwargs))
                return FakeEmbedding()

        result = get_local_embedding("total sales", FakeEmbedder())

        self.assertEqual(len(result), EMBED_DIM)
        self.assertEqual(result[0], 0.25)
        self.assertEqual(calls, [(
            "total sales",
            {
                "normalize_embeddings": True,
                "convert_to_numpy": True,
                "show_progress_bar": False,
            },
        )])

    def test_local_embedding_preserves_degraded_mode_for_bad_dimensions(self):
        class FakeEmbedder:
            def encode(self, _text, **_kwargs):
                return [0.25]

        self.assertEqual(get_local_embedding("total sales", FakeEmbedder()), [0.0] * EMBED_DIM)

    def test_ingestion_rejects_zero_vectors(self):
        with self.assertRaisesRegex(RuntimeError, "refusing to persist invalid embeddings"):
            _require_nonzero_embedding([0.0] * EMBED_DIM)


if __name__ == "__main__":
    unittest.main()
