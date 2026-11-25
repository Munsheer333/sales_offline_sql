import json
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
report_path = ROOT_DIR / "benchmark" / "results" / "accuracy_report.json"

with open(report_path, "r") as f:
    data = json.load(f)

mismatches = []
for item in data.get("details", []):
    if item["sql"]["match"] and not item["execution"]["match"]:
        if not item["sql"]["generated"].startswith("OFF_TOPIC"):
            mismatches.append(item)

print("Total non-off-topic mismatches (SQL match, execution fail):", len(mismatches))
for item in mismatches:
    print("\nQuestion:", item["question"])
    print("Expected:", item["sql"]["expected"])
    print("Generated:", item["sql"]["generated"])
