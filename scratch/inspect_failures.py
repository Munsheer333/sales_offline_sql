import json
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
report_path = ROOT_DIR / "benchmark" / "results" / "accuracy_report.json"

if not report_path.exists():
    print("Report path does not exist.")
    exit(1)

with open(report_path, "r") as f:
    data = json.load(f)

details = data.get("details", [])
print(f"Total details loaded: {len(details)}")

failed_execution = [d for d in details if not d["execution"]["match"]]
print(f"Total failed execution: {len(failed_execution)}")

# Group by category
from collections import defaultdict
by_cat = defaultdict(list)
for d in failed_execution:
    by_cat[d["category"]].append(d)

for cat, items in by_cat.items():
    print(f"\n=========================================")
    print(f" CATEGORY: {cat.upper()} ({len(items)} failures)")
    print(f"=========================================")
    for idx, item in enumerate(items[:5]):
        print(f"\n{idx+1}. Question: {item['question']}")
        print(f"   Expected SQL: {item['sql']['expected']}")
        print(f"   Generated SQL: {item['sql']['generated']}")
        print(f"   Error: {item['error']}")
