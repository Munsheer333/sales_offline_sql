import unittest
from core.query_normalizer import normalize_shorthand_query, detect_incomplete_question

class QueryNormalizerTests(unittest.TestCase):
    def test_detects_incomplete_hpcl_sales(self):
        msg = detect_incomplete_question("HPCL sales")
        self.assertIsNotNone(msg)
        self.assertIn("incomplete", msg.lower())
        self.assertIn("HPCL", msg)

    def test_detects_incomplete_retail_performance(self):
        msg = detect_incomplete_question("Retail performance")
        self.assertIsNotNone(msg)
        self.assertIn("missing or ambiguous", msg.lower())
        self.assertIn("Retail", msg)

    def test_detects_incomplete_top_company(self):
        msg = detect_incomplete_question("Top company")
        self.assertIsNotNone(msg)
        self.assertIn("ranking metric is missing", msg.lower())

    def test_complete_query_returns_none(self):
        msg = detect_incomplete_question("What is total sales for HPCL in FY 2024-2025?")
        self.assertIsNone(msg)

    def test_normalizes_shorthand_company_sales(self):
        normalized = normalize_shorthand_query("HPCL sales")
        self.assertEqual(normalized, "What is total sales for HPCL in current fiscal year")

    def test_normalizes_shorthand_product_growth(self):
        normalized = normalize_shorthand_query("HSD growth")
        self.assertEqual(normalized, "What is growth % for HSD in current fiscal year")

    def test_normalizes_shorthand_sbu_market_share(self):
        normalized = normalize_shorthand_query("Retail market share")
        self.assertEqual(normalized, "What is market share for Retail in current fiscal year")

    def test_normalizes_shorthand_zone_sales_difference(self):
        normalized = normalize_shorthand_query("North zone sales difference")
        self.assertEqual(normalized, "What is sales difference for North zone in current fiscal year")

    def test_ignores_full_sentence_queries(self):
        q = "What is total sales for HPCL?"
        normalized = normalize_shorthand_query(q)
        self.assertEqual(normalized, q)

    def test_ignores_unknown_shorthand(self):
        q = "some random query"
        normalized = normalize_shorthand_query(q)
        self.assertEqual(normalized, q)

if __name__ == "__main__":
    unittest.main()
