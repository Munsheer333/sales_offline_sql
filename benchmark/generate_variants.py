import os
import json

VARIANTS_MAP = {
  "What is the actual sales in TMT for Retail HSD from Dec 1 2025 to Dec 15 2025?": {
    "paraphrases": [
      "Get the actual sales volume in TMT for Retail SBU HSD product from December 1, 2025 to December 15, 2025.",
      "How much Retail SBU HSD in TMT was actually sold between Dec 1 2025 and Dec 15 2025?",
      "Provide the actual TMT sales of HSD under Retail from Dec 1 2025 to Dec 15 2025."
    ],
    "abbreviations": [
      "Actual Retail HSD sales TMT Dec 1-15 2025.",
      "Retail HSD TMT sales Dec 1 2025 to Dec 15 2025."
    ],
    "synonyms": [
      "What is the actual volume in TMT for Retail HSD from Dec 1 2025 to Dec 15 2025?",
      "What is the actual business in TMT for Retail HSD from Dec 1 2025 to Dec 15 2025?"
    ]
  },
  "Show actual sales for product MS in MT for Retail in Oct 2025.": {
    "paraphrases": [
      "Display the actual sales of MS product in MT for Retail SBU in October 2025.",
      "What are the actual sales in MT for MS in Retail SBU during Oct 2025?",
      "Provide actual volume of MS sold in MT for Retail in Oct 2025."
    ],
    "abbreviations": [
      "Actual MS sales MT Retail Oct 2025.",
      "Retail MS actual sales Oct 2025."
    ],
    "synonyms": [
      "Show actual turnover for product MS in MT for Retail in Oct 2025.",
      "Show actual volume for product MS in MT for Retail in Oct 2025."
    ]
  },
  "What is market share for IOCL in FY 2024-2025?": {
    "paraphrases": [
      "How much is the market share percentage of IOCL during the fiscal year 2024-2025?",
      "What percentage of market share does IOCL hold in FY 2024-2025?",
      "Show IOCL's market contribution for the fiscal year 2024-2025."
    ],
    "abbreviations": [
      "IOCL market share FY 2024-2025.",
      "Market share IOCL FY 24-25."
    ],
    "synonyms": [
      "What is share for IOCL in FY 2024-2025?",
      "What is contribution % for IOCL in FY 2024-2025?"
    ]
  },
  "Which company has the lowest market share in fiscal year 2024-2025?": {
    "paraphrases": [
      "Find the company that holds the smallest market share in FY 2024-2025.",
      "In fiscal year 2024-2025, which company recorded the minimum market share?",
      "Show the company with the lowest market contribution in FY 2024-2025."
    ],
    "abbreviations": [
      "Lowest market share company FY 2024-2025.",
      "Min market share company FY 24-25."
    ],
    "synonyms": [
      "Which company has the lowest share in fiscal year 2024-2025?",
      "Which company has the lowest contribution % in fiscal year 2024-2025?"
    ]
  },
  "What is the sales growth percentage for HPCL in Retail SBU from FY 2023-2024 to FY 2024-2025?": {
    "paraphrases": [
      "By what percentage did HPCL sales grow in Retail SBU between FY 2023-2024 and FY 2024-2025?",
      "Calculate the sales growth rate for HPCL in the Retail SBU from FY 2023-2024 to FY 2024-2025.",
      "What is the rate of sales growth for HPCL in Retail SBU comparing FY 2023-2024 to FY 2024-2025?"
    ],
    "abbreviations": [
      "HPCL Retail sales growth % FY24 to FY25.",
      "Sales growth % HPCL Retail SBU FY 23-24 to 24-25."
    ],
    "synonyms": [
      "What is the sales increase percentage for HPCL in Retail SBU from FY 2023-2024 to FY 2024-2025?",
      "What is the volume growth percentage for HPCL in Retail SBU from FY 2023-2024 to FY 2024-2025?"
    ]
  },
  "Show products having growth of more than 5% in actual volume in current fiscal year.": {
    "paraphrases": [
      "Which products grew by more than 5% in actual volume during the current fiscal year?",
      "List all products with actual volume growth exceeding 5% in the current fiscal year.",
      "Display products whose actual volume growth is greater than 5% in the current fiscal year."
    ],
    "abbreviations": [
      "Products with >5% volume growth current FY.",
      "Volume growth >5% products current FY."
    ],
    "synonyms": [
      "Show products having increase of more than 5% in actual volume in current fiscal year.",
      "Show products having improvement of more than 5% in actual volume in current fiscal year."
    ]
  },
  "Rank companies by industry performance sales in AP state.": {
    "paraphrases": [
      "Provide a ranking of companies based on industry performance sales within AP state.",
      "Show the rank list of companies for industry performance sales in Andhra Pradesh.",
      "Order companies by their industry performance sales volume in AP state."
    ],
    "abbreviations": [
      "Rank companies by sales in AP state.",
      "Company ranking industry sales AP."
    ],
    "synonyms": [
      "Rank companies by industry performance volume in AP state.",
      "Rank companies by industry performance turnover in AP state."
    ]
  },
  "Get the top 3 products by actual weight in current fiscal year.": {
    "paraphrases": [
      "Find the 3 products with the highest actual weight in the current fiscal year.",
      "What are the top three products ranked by actual weight for the current fiscal year?",
      "List the top 3 products based on their actual weight during the current fiscal year."
    ],
    "abbreviations": [
      "Top 3 products by actual weight current FY.",
      "Best 3 products by actual weight current fiscal year."
    ],
    "synonyms": [
      "Get the highest 3 products by actual weight in current fiscal year.",
      "Get the largest 3 products by actual weight in current fiscal year."
    ]
  },
  "Get the total target quantity in KL from M60_LEVEL for strategic business unit I&C in North zone.": {
    "paraphrases": [
      "What is the sum of target quantity in KL in M60_LEVEL for SBU I&C in the North zone?",
      "Find the total target quantity in KL for strategic business unit I&C located in North zone from M60_LEVEL.",
      "Retrieve total target quantity in KL for SBU I&C in North zone."
    ],
    "abbreviations": [
      "Target qty KL M60_LEVEL I&C North zone.",
      "Total target KL I&C SBU North."
    ],
    "synonyms": [
      "Get the sum target quantity in KL from M60_LEVEL for strategic business unit I&C in North zone.",
      "Get the total target volume in KL from M60_LEVEL for strategic business unit I&C in North zone."
    ]
  },
  "Compare HPCL and IOCL sales in West zone for FY 2024-2025.": {
    "paraphrases": [
      "Provide a sales comparison between HPCL and IOCL in the West zone for the fiscal year 2024-2025.",
      "How do HPCL and IOCL sales compare in the West zone during FY 2024-2025?",
      "Show HPCL vs IOCL sales performance in West zone for FY 2024-2025."
    ],
    "abbreviations": [
      "Compare HPCL & IOCL sales West zone FY25.",
      "HPCL vs IOCL sales West zone FY 2024-2025."
    ],
    "synonyms": [
      "Compare HPCL and IOCL volume in West zone for FY 2024-2025.",
      "Compare HPCL and IOCL business in West zone for FY 2024-2025."
    ]
  },
  "Show month-wise actual sales trend in TMT for Retail MS in FY 2024-2025.": {
    "paraphrases": [
      "Display the month-on-month actual sales trend in TMT for Retail SBU MS product in FY 2024-2025.",
      "What is the monthly trend of actual sales in TMT for Retail MS during FY 2024-2025?",
      "Provide the monthly actual sales trend in TMT of MS under Retail for FY 2024-2025."
    ],
    "abbreviations": [
      "Monthly actual MS sales trend TMT Retail FY25.",
      "Retail MS sales trend TMT month-wise FY 2024-2025."
    ],
    "synonyms": [
      "Show month-wise actual volume trend in TMT for Retail MS in FY 2024-2025.",
      "Show month-wise actual business trend in TMT for Retail MS in FY 2024-2025."
    ]
  },
  "Get day-wise actual sales for LPG in strategic business unit LPG from Dec 1 2025 to Dec 10 2025.": {
    "paraphrases": [
      "Find the daily actual sales for LPG in SBU LPG from December 1, 2025 to December 10, 2025.",
      "Retrieve daily actual sales of LPG in strategic business unit LPG between Dec 1 2025 and Dec 10 2025.",
      "Show day-by-day actual sales of LPG under LPG SBU from Dec 1 2025 to Dec 10 2025."
    ],
    "abbreviations": [
      "Daily LPG sales LPG SBU Dec 1-10 2025.",
      "LPG daily actual sales LPG SBU Dec 1 2025 to Dec 10 2025."
    ],
    "synonyms": [
      "Get day-wise actual volume for LPG in strategic business unit LPG from Dec 1 2025 to Dec 10 2025.",
      "Get day-wise actual business for LPG in strategic business unit LPG from Dec 1 2025 to Dec 10 2025."
    ]
  },
  "Show industry performance for HPCL in district AGRA for fiscal year 2021-2022.": {
    "paraphrases": [
      "What is the industry performance of HPCL in AGRA district during the fiscal year 2021-2022?",
      "Display industry performance statistics for HPCL in Agra district for FY 2021-2022.",
      "Provide the industry performance of HPCL within district AGRA in FY 2021-2022."
    ],
    "abbreviations": [
      "HPCL industry performance AGRA district FY22.",
      "Industry performance HPCL AGRA FY 2021-2022."
    ],
    "synonyms": [
      "Show industry contribution for HPCL in district AGRA for fiscal year 2021-2022.",
      "Show industry volume for HPCL in district AGRA for fiscal year 2021-2022."
    ]
  },
  "Get the total actual sales in TMT by zone for Retail in FY 2024-2025.": {
    "paraphrases": [
      "What are the total actual sales in TMT grouped by zone for Retail SBU in FY 2024-2025?",
      "Find the total actual sales in TMT by zone for the Retail SBU in fiscal year 2024-2025.",
      "Retrieve zonal total actual sales in TMT for Retail in FY 2024-2025."
    ],
    "abbreviations": [
      "Zonal actual Retail sales TMT FY25.",
      "Total actual sales TMT by zone Retail FY 2024-2025."
    ],
    "synonyms": [
      "Get the total actual volume in TMT by zone for Retail in FY 2024-2025.",
      "Get the total actual business in TMT by zone for Retail in FY 2024-2025."
    ]
  }
}

def generate_and_save_variants():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    golden_path = os.path.join(base_dir, "templates", "golden_queries.json")
    
    # Store lists of prompts for each type separately
    paraphrase_list = []
    abbreviated_list = []
    synonym_list = []
    
    if not os.path.exists(golden_path):
        print(f"Error: Golden queries not found at {golden_path}")
        return
        
    with open(golden_path, "r", encoding="utf-8") as f:
        golden_queries = json.load(f)
        
    for item in golden_queries:
        question = item["question"]
        category = item["category"]
        sql = item["sql"]
        complexity = item.get("complexity", "MEDIUM")
        
        variants = VARIANTS_MAP.get(question)
        if not variants:
            print(f"Warning: No variants mapped for question '{question}'")
            continue
            
        # 1. Paraphrases (3 items)
        for pq in variants["paraphrases"]:
            paraphrase_list.append({
                "original_question": question,
                "category": category,
                "question": pq,
                "sql": sql,
                "complexity": complexity
            })
            
        # 2. Abbreviations (2 items)
        for aq in variants["abbreviations"]:
            abbreviated_list.append({
                "original_question": question,
                "category": category,
                "question": aq,
                "sql": sql,
                "complexity": complexity
            })
            
        # 3. Synonyms (2 items)
        for sq in variants["synonyms"]:
            synonym_list.append({
                "original_question": question,
                "category": category,
                "question": sq,
                "sql": sql,
                "complexity": complexity
            })
            
    # Save separately
    para_path = os.path.join(base_dir, "paraphrases.json")
    abbr_path = os.path.join(base_dir, "abbreviations.json")
    syn_path = os.path.join(base_dir, "synonym_variants.json")
    
    with open(para_path, "w", encoding="utf-8") as f:
        json.dump(paraphrase_list, f, indent=2)
    with open(abbr_path, "w", encoding="utf-8") as f:
        json.dump(abbreviated_list, f, indent=2)
    with open(syn_path, "w", encoding="utf-8") as f:
        json.dump(synonym_list, f, indent=2)
        
    print(f"Generated and saved:")
    print(f"  - {len(paraphrase_list)} paraphrases in {para_path}")
    print(f"  - {len(abbreviated_list)} abbreviations in {abbr_path}")
    print(f"  - {len(synonym_list)} synonym variants in {syn_path}")

if __name__ == "__main__":
    generate_and_save_variants()
