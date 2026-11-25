import os
import json
from typing import Dict, Any, List, Optional

class MetadataRegistry:
    def __init__(self, registry_path: str = None):
        if registry_path is None:
            # Resolve relative to current file
            base_dir = os.path.dirname(os.path.abspath(__file__))
            registry_path = os.path.join(base_dir, "metadata_registry.json")
            
        self.registry_path = registry_path
        self.data: Dict[str, Any] = {}
        self.load()

    def load(self):
        if os.path.exists(self.registry_path):
            with open(self.registry_path, "r", encoding="utf-8") as f:
                self.data = json.load(f)
        else:
            # Fallback/default empty structure
            self.data = {"metrics": {}, "tables": {}}

    def get_metric(self, name_or_alias: str) -> Optional[Dict[str, Any]]:
        # Clean input
        term = str(name_or_alias or "").strip().lower()
        if not term:
            return None
            
        # Direct check
        metrics = self.data.get("metrics", {})
        if term in metrics:
            return metrics[term]
            
        # Alias lookup
        for metric_name, details in metrics.items():
            aliases = [a.lower() for a in details.get("aliases", [])]
            if term == metric_name.lower() or term in aliases:
                return {
                    "name": metric_name,
                    "column": details.get("column"),
                    "aliases": details.get("aliases"),
                    "business_definition": details.get("business_definition")
                }
        return None

    def get_table(self, table_name: str) -> Optional[Dict[str, Any]]:
        return self.data.get("tables", {}).get(table_name)

    def get_column(self, table_name: str, column_name: str) -> Optional[Dict[str, Any]]:
        table = self.get_table(table_name)
        if table:
            return table.get("columns", {}).get(column_name)
        return None

    def get_all_metrics(self) -> Dict[str, Any]:
        return self.data.get("metrics", {})

    def get_all_tables(self) -> Dict[str, Any]:
        return self.data.get("tables", {})
