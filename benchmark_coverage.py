"""Run the generated deterministic coverage benchmark against local datasets."""

import argparse
import json
from pathlib import Path

from config import DB_URL
from core.phase7_coverage_matrix import (
    entity_catalog_for_matrix,
    evaluate_pattern_coverage,
    generate_coverage_cases,
    load_coverage_catalog,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute-limit", type=int, default=100)
    parser.add_argument("--output", default="data/coverage_report.json")
    args = parser.parse_args()

    schema = json.loads(Path("data/schema_metadata.json").read_text())
    catalog = load_coverage_catalog(DB_URL)
    cases = generate_coverage_cases(catalog)
    report = evaluate_pattern_coverage(
        cases,
        schema,
        entity_catalog=entity_catalog_for_matrix(catalog),
        conn_str=DB_URL,
        execute_limit=max(0, args.execute_limit),
    )
    Path(args.output).write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"Generated cases: {report['total_cases']}")
    print(f"Deterministic coverage: {report['matched_cases']}/{report['total_cases']} ({report['coverage_percentage']}%)")
    print(f"Executed SQL sample: {report['executed_sample']}, failed: {report['execution_failed']}")
    print(f"Semantic validation failures: {report['semantic_failed']}")
    for family, result in report["families"].items():
        print(f"- {family}: {result['matched']}/{result['total']} ({result['coverage_percentage']}%)")
    if report["failed_cases"]:
        print(f"Failure details written to {args.output}")


if __name__ == "__main__":
    main()
