import os
import re
from typing import Dict, Any, Tuple, List, Optional

class SQLAssembler:
    def __init__(self, templates_dir: str = None):
        if templates_dir is None:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            self.templates_dir = os.path.join(base_dir, "sql_templates")
        else:
            self.templates_dir = templates_dir
            
        self.templates: Dict[str, str] = {}
        self.load_templates()

        # Database column mapping by table
        self.table_columns = {
            "industry_performance": {
                "company": "comname",
                "state": "statename",
                "category": "category",
                "month": "month_name",
                "fiscal_year": "fiscal_year"
            },
            "m60_level": {
                "sbu": "sbu_name",
                "zone": "zone_name",
                "region": "region_name",
                "salesarea": "salesarea_name",
                "product": "productname",
                "month": "month_name",
                "fiscal_year": "fiscal_year"
            }
        }

    def load_templates(self):
        if os.path.exists(self.templates_dir):
            for filename in os.listdir(self.templates_dir):
                if filename.endswith(".sql"):
                    name = filename.replace(".sql", "")
                    with open(os.path.join(self.templates_dir, filename), "r", encoding="utf-8") as f:
                        self.templates[name] = f.read()

    def _determine_table(self, filters: Dict[str, Any], dimension: str, dimensions: Optional[List[str]] = None) -> str:
        industry_keys = {"company", "state"}
        keys_present = set(filters.keys())
        if dimension:
            keys_present.add(dimension)
        if dimensions:
            keys_present.update(dimensions)
            
        if keys_present.intersection(industry_keys):
            return "industry_performance"
        if "category" in filters and "sbu" not in filters:
            return "industry_performance"
        return "m60_level"

    def _format_value(self, key: str, val: str, table: Optional[str] = None, question: Optional[str] = None) -> str:
        val_str = str(val).strip()
        if key == "zone" and table in ("m60_level", "mom_level"):
            q_norm = question.lower() if question else ""
            if "hpcl mkt" not in q_norm:
                if val_str in ("North West Frontier", "North Frontier Zone", "North Frontier"):
                    val_str = "North"
                elif val_str in ("South Western Zone", "South Western"):
                    val_str = "South"
                    
        if key in ("company", "state", "category", "product", "zone", "region", "sbu", "salesarea"):
            # Map standard abbreviations
            abbrev_map = {
                "AP": "Andhra Pradesh",
                "UP": "Uttar Pradesh",
                "MP": "Madhya Pradesh",
                "TN": "Tamil Nadu",
                "HP": "Himachal Pradesh",
                "MH": "Maharashtra",
                "KA": "Karnataka",
                "KL": "Kerala",
                "DL": "Delhi",
                "RJ": "Rajasthan",
                "GJ": "Gujarat",
                "WB": "West Bengal",
                "JK": "Jammu & Kashmir",
                "PB": "Punjab",
                "HR": "Haryana",
                "UK": "Uttarakhand",
                "JH": "Jharkhand",
                "CG": "Chhattisgarh",
                "OD": "Odisha",
                "TS": "Telangana",
                "AS": "Assam",
                "BR": "Bihar",
                "GA": "Goa"
            }
            mapped_val = abbrev_map.get(val_str.upper(), val_str)
            return f"'{mapped_val.upper()}'"
        elif key == "fiscal_year":
            cleaned = re.sub(r'^(?:fy|FY)\s*', '', val_str).strip()
            # Format YYYY-YY to YYYY-YYYY
            if len(cleaned) == 7 and cleaned[4] == "-":
                year_part = int(cleaned[:4])
                return f"'{year_part}-{year_part + 1}'"
            return f"'{cleaned}'"
        elif key == "month":
            return f"'{val_str.title()}'"
        return f"'{val_str}'"

    def _build_filters(self, table: str, filters: Dict[str, Any], comparison: List[str], dimension: Optional[str] = None, question: Optional[str] = None, comparison_dimension: Optional[str] = None) -> List[str]:
        conds = []
        col_map = self.table_columns[table]
        comp_dim = comparison_dimension or dimension
        
        # Build regular filters (never filter by dimension or comparison dimension)
        for key, val in filters.items():
            if key == dimension or key == comp_dim:
                continue
            if key in col_map:
                col = col_map[key]
                conds.append(f"{col} = {self._format_value(key, val, table, question)}")
                
        # Build comparison filter if applicable
        if comp_dim and comparison and comp_dim in col_map:
            col = col_map[comp_dim]
            formatted_vals = [self._format_value(comp_dim, val, table, question) for val in comparison]
            conds.append(f"{col} IN ({', '.join(formatted_vals)})")
            
        return conds

    def _get_ranking_order_and_limit(self, question: str) -> Tuple[Optional[str], Optional[int]]:
        if not question:
            return None, None
        q_lower = question.lower()
        
        desc_terms = ["most", "highest", "top", "largest", "maximum", "max", "best", "greatest", "leading"]
        asc_terms = ["least", "lowest", "bottom", "smallest", "minimum", "min", "worst", "fewest"]
        
        order = None
        if any(re.search(rf"\b{term}\b", q_lower) for term in desc_terms):
            order = "DESC"
        elif any(re.search(rf"\b{term}\b", q_lower) for term in asc_terms):
            order = "ASC"
            
        if not order:
            return None, None
            
        limit = 1
        num_match = re.search(r"\b(\d+)\b", q_lower)
        if num_match:
            limit = int(num_match.group(1))
        elif any(re.search(rf"\b{term}\b", q_lower) for term in ["top", "bottom"]):
            if not re.search(r"\b(which|what|who)\b", q_lower):
                limit = 5
                
        return order, limit

    def assemble(self, plan: Dict[str, Any], question: Optional[str] = None) -> str:
        intent = plan.get("intent", "TOTAL_SALES").upper()
        dimension = plan.get("dimension")
        dimensions = plan.get("dimensions")
        if not dimensions and dimension:
            dimensions = [dimension]
        elif not dimensions:
            dimensions = []
        comparison_dimension = plan.get("comparison_dimension") or dimension
        filters_dict = plan.get("filters", {})
        comparison = plan.get("comparison", [])
        
        # Determine Table
        table = self._determine_table(filters_dict, dimension, dimensions)
        col_map = self.table_columns[table]
        
        # Build filter list
        filter_list = self._build_filters(table, filters_dict, comparison, dimension, question, comparison_dimension)
        if table == "industry_performance":
            filter_list.append("(category != 'O' OR category IS NULL)")
        filters_str = " AND ".join(filter_list) if filter_list else "1=1"

        # Determine dimensions placeholder values
        dim_cols = [col_map[d] for d in dimensions if d in col_map]
        
        if dim_cols:
            dimensions_select = f"{', '.join(dim_cols)},"
            dimensions_group_by = f"GROUP BY {', '.join(dim_cols)}"
            dimensions_output = f"scoped_sales.{dim_cols[0]},"
            dimension_join = f"JOIN market_sales ON scoped_sales.{dim_cols[0]} = market_sales.{dim_cols[0]}"
        else:
            dimensions_select = ""
            dimensions_group_by = ""
            dimensions_output = ""
            dimension_join = "CROSS JOIN market_sales"
        dim_col = dim_cols[0] if dim_cols else None

        # Assemble based on intent
        if intent == "COMPARE":
            template = self.templates.get("comparison", "")
            if len(dim_cols) > 1:
                order_by_str = f"ORDER BY {', '.join(dim_cols)}"
            elif dimension in ("fiscal_year", "month") and dim_col:
                order_by_str = f"ORDER BY {dim_col}"
            else:
                order_by_str = "ORDER BY total_sales_tmt DESC"

            sql = template.replace("{{source_table}}", table)\
                         .replace("{{filters}}", filters_str)\
                         .replace("{{dimensions_select}}", dimensions_select)\
                         .replace("{{dimensions_group_by}}", dimensions_group_by)\
                         .replace("{{order_by}}", order_by_str)
            
        elif intent in ("TOP_N", "BOTTOM_N", "RANKING"):
            template = self.templates.get("ranking", "")
            
            # Map order direction
            order = "ASC" if intent == "BOTTOM_N" else "DESC"
            
            # Determine limit (default to 5 for N lists, 10 for standard rankings)
            limit = 5 if intent in ("TOP_N", "BOTTOM_N") else 10
            if question:
                limit_match = re.search(
                    r"\b(?:top|bottom|highest|lowest|largest|smallest|first|last)\s+(\d{1,3})\b",
                    question.lower(),
                )
                if limit_match:
                    limit = int(limit_match.group(1))
            
            sql = template.replace("{{source_table}}", table)\
                         .replace("{{filters}}", filters_str)\
                         .replace("{{dimensions_select}}", dimensions_select)\
                         .replace("{{dimensions_group_by}}", dimensions_group_by)\
                         .replace("{{order_direction}}", order)\
                         .replace("{{limit}}", str(limit))
            
        elif intent == "TREND":
            template = self.templates.get("trend", "")
            sql = template.replace("{{source_table}}", table)\
                         .replace("{{filters}}", filters_str)
            
        elif intent in ("MOM", "YOY", "GROWTH"):
            template = self.templates.get("growth", "")
            
            # Resolve fiscal years
            fy_val = filters_dict.get("fiscal_year", "2024-25")
            if "-" in fy_val:
                parts = fy_val.split("-")
                start = int(parts[0])
            else:
                start = int(fy_val)
            prev_yr = f"'{start - 1}-{start}'"
            curr_yr = f"'{start}-{start + 1}'"
            
            # Remove fiscal_year from filter list for growth calculation since we need both years
            growth_filter_list = [c for c in filter_list if "fiscal_year =" not in c]
            growth_filters_str = " AND ".join(growth_filter_list) if growth_filter_list else "1=1"
            
            sql = template.replace("{{source_table}}", table)\
                         .replace("{{filters}}", growth_filters_str)\
                         .replace("{{dimensions_select}}", dimensions_select)\
                         .replace("{{dimensions_group_by}}", dimensions_group_by)\
                         .replace("{{previous_year}}", prev_yr)\
                         .replace("{{current_year}}", curr_yr)
            
        elif intent in ("MARKET_SHARE", "CONTRIBUTION"):
            template = self.templates.get("market_share", "")
            
            if intent == "CONTRIBUTION":
                # For contribution, denominator has the exact same filters as numerator,
                # but is NOT grouped by the dimension.
                market_filters_str = filters_str
                market_dimensions_select = ""
                market_dimensions_group_by = ""
                actual_dimension_join = "CROSS JOIN market_sales"
            else: # MARKET_SHARE
                # Market filters exclude specific filter on matching entity (e.g. company, category, product)
                exclude_keys = {"company", "category", "product"}
                market_filter_list = []
                for key, val in filters_dict.items():
                    if key not in exclude_keys and key in col_map:
                        market_filter_list.append(f"{col_map[key]} = {self._format_value(key, val, table, question)}")
                if table == "industry_performance":
                    market_filter_list.append("(category != 'O' OR category IS NULL)")
                market_filters_str = " AND ".join(market_filter_list) if market_filter_list else "1=1"
                
                # Check if we have any entity filter (company, category, product) in filters_dict
                # that is NOT the grouping dimension itself.
                has_other_entity_filter = False
                for key in filters_dict:
                    if key in exclude_keys and key != dimension:
                        has_other_entity_filter = True
                        break
                
                if has_other_entity_filter and dim_col:
                    market_dimensions_select = dimensions_select
                    market_dimensions_group_by = dimensions_group_by
                    actual_dimension_join = dimension_join
                else:
                    market_dimensions_select = ""
                    market_dimensions_group_by = ""
                    actual_dimension_join = "CROSS JOIN market_sales"
            
            sql = template.replace("{{source_table}}", table)\
                         .replace("{{filters}}", filters_str)\
                         .replace("{{market_filters}}", market_filters_str)\
                         .replace("{{dimensions_select}}", dimensions_select)\
                         .replace("{{dimensions_group_by}}", dimensions_group_by)\
                         .replace("{{market_dimensions_select}}", market_dimensions_select)\
                         .replace("{{market_dimensions_group_by}}", market_dimensions_group_by)\
                         .replace("{{dimensions_output}}", dimensions_output)\
                         .replace("{{dimension_join}}", actual_dimension_join)
            
            # Apply sorting and limit if ranking terms are found
            if question:
                order, limit = self._get_ranking_order_and_limit(question)
                if order and limit:
                    sql = sql.rstrip().rstrip(";")
                    sql += f"\nORDER BY market_share_percentage {order}\nLIMIT {limit};"
            
        else: # TOTAL_SALES or default fallback
            template = self.templates.get("total_sales", "")
            sql = template.replace("{{source_table}}", table)\
                         .replace("{{filters}}", filters_str)\
                         .replace("{{dimensions_select}}", dimensions_select)\
                         .replace("{{dimensions_group_by}}", dimensions_group_by)
            
        # Clean up any trailing commas or redundant whitespaces
        sql = re.sub(r',\s*FROM', '\nFROM', sql)
        sql = re.sub(r'WHERE\s*$', '', sql)
        return sql.strip()
