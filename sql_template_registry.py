"""Load external SQL templates without embedding SQL in business logic.

Usage:
    python sql_template_registry.py --list
    python sql_template_registry.py --show growth
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


TEMPLATE_DIRECTORY = Path(__file__).with_name("sql_templates")
REGISTRY_PATH = TEMPLATE_DIRECTORY / "registry.json"


def load_registry(registry_path: Path = REGISTRY_PATH) -> dict[str, dict[str, Any]]:
    return json.loads(registry_path.read_text(encoding="utf-8"))


def list_templates(registry_path: Path = REGISTRY_PATH) -> list[str]:
    return sorted(load_registry(registry_path))


def load_template(name: str, registry_path: Path = REGISTRY_PATH) -> dict[str, Any]:
    registry = load_registry(registry_path)
    if name not in registry:
        raise KeyError(f"Unknown SQL template: {name}")

    metadata = registry[name]
    template_path = registry_path.parent / metadata["file"]
    template_sql = template_path.read_text(encoding="utf-8").strip()
    actual_tokens = sorted(set(re.findall(r"\{\{([a-z_]+)\}\}", template_sql)))
    required_tokens = sorted(metadata.get("required_tokens", []))
    missing_tokens = sorted(set(required_tokens) - set(actual_tokens))
    if missing_tokens:
        raise ValueError(f"Template {name!r} is missing required tokens: {missing_tokens}")

    return {
        "name": name,
        "description": metadata.get("description", ""),
        "path": str(template_path),
        "required_tokens": required_tokens,
        "available_tokens": actual_tokens,
        "template": template_sql,
    }


def validate_registry(registry_path: Path = REGISTRY_PATH) -> dict[str, Any]:
    templates = list_templates(registry_path)
    loaded = [load_template(name, registry_path) for name in templates]
    return {"valid": True, "templates": [item["name"] for item in loaded], "count": len(loaded)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect the external SQL template registry.")
    parser.add_argument("--list", action="store_true", help="List registered template names.")
    parser.add_argument("--show", help="Show one registered template.")
    parser.add_argument("--validate", action="store_true", help="Validate all registry entries.")
    args = parser.parse_args()

    if args.list:
        print(json.dumps(list_templates(), indent=2))
    if args.show:
        print(json.dumps(load_template(args.show), indent=2))
    if args.validate:
        print(json.dumps(validate_registry(), indent=2))
    if not args.list and not args.show and not args.validate:
        parser.error("use --list, --show, or --validate")


if __name__ == "__main__":
    main()
