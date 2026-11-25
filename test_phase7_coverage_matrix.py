import json
import unittest
from pathlib import Path

from core.business_entity_catalog import analyze_business_entities
from core.phase7_coverage_matrix import (
    entity_catalog_for_matrix,
    evaluate_pattern_coverage,
    generate_coverage_cases,
)


class CoverageMatrixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = json.loads(Path("data/schema_metadata.json").read_text())
        cls.catalog = {
            "industry_companies": ["HPCL"],
            "industry_sbus": ["LPG"],
            "industry_products": ["LPG-BULK"],
            "industry_zones": ["SZ"],
            "industry_years": ["2023-2024", "2024-2025"],
            "mom_years": ["2023-2024", "2024-2025"],
            "m60_sbus": ["LPG"],
            "m60_products": ["LPG"],
            "m60_zones": ["South"],
        }

    def test_generated_matrix_covers_multiple_reusable_families(self):
        cases = generate_coverage_cases(self.catalog)

        self.assertGreater(len(cases), 5)
        self.assertIn("industry_sbu_zone_growth", {item["family"] for item in cases})
        self.assertIn("m60_target_quantity", {item["family"] for item in cases})
        self.assertIn("mom_product_growth_threshold", {item["family"] for item in cases})

    def test_small_generated_matrix_matches_expected_templates(self):
        cases = generate_coverage_cases(self.catalog)
        report = evaluate_pattern_coverage(
            cases,
            self.schema,
            entity_catalog=entity_catalog_for_matrix(self.catalog),
        )

        self.assertEqual(report["failed_cases"], 0, report["failures"])

    def test_lpg_product_context_does_not_create_sbu_entity(self):
        analysis = analyze_business_entities(
            "Get target quantity for product LPG.",
            catalog={"sbu": ["LPG"], "product": ["LPG"]},
        )

        self.assertIn("product", {item["type"] for item in analysis["matches"]})
        self.assertNotIn("sbu", {item["type"] for item in analysis["matches"]})

    def test_lpg_can_be_both_explicit_sbu_and_explicit_product(self):
        analysis = analyze_business_entities(
            "Get target quantity for LPG SBU for product LPG.",
            catalog={"sbu": ["LPG"], "product": ["LPG"]},
        )

        self.assertEqual({item["type"] for item in analysis["matches"]}, {"sbu", "product"})


if __name__ == "__main__":
    unittest.main()
