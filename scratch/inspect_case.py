import json
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
report_path = ROOT_DIR / "benchmark" / "results" / "accuracy_report.json"

with open(report_path, "r") as f:
    data = json.load(f)

for item in data.get("details", []):
    if "Retail LPG in FY 2021-2022" in item["question"]:
        print(json.dumps(item, indent=2))
