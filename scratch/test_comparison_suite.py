from core.logical_planner import LogicalQueryPlanner
from core.sql_assembler import SQLAssembler

p = LogicalQueryPlanner()
a = SQLAssembler()

queries = [
    "Compare product performance between two fiscal years.",
    "Compare product performance between FY 2023-2024 and FY 2024-2025.",
    "Compare company performance between two fiscal years.",
    "Compare sbu performance between two fiscal years.",
    "Compare state performance between two fiscal years.",
    "Compare FY 2023-2024 and FY 2024-2025 sales.",
    "Compare LUBES and TMT sales.",
    "Compare IOCL and BPCL sales.",
    "Compare North and South sales.",
    "Compare April and May sales."
]

for q in queries:
    plan = p.plan(q)
    sql = a.assemble(plan, q)
    print(f"=== Query: {q} ===")
    print(f"Plan: intent={plan['intent']}, dim={plan['dimension']}, dims={plan.get('dimensions')}, comp_dim={plan.get('comparison_dimension')}, comp={plan.get('comparison')}")
    print(sql)
    print()
