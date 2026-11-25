"""
NLPSQL SYSTEM - PHASE 03C
Graph-Based Relationship Planner

Builds a relationship graph from schema foreign-key metadata and creates
deterministic join paths for logical query plans. The planner is conservative:
it never invents joins from matching column names alone.
"""

import asyncio
import heapq
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


IDENTIFIER_RE = re.compile(r"^[a-z_][a-z0-9_]*$")


class RelationshipPlanningError(ValueError):
    """Raised when a safe deterministic join path cannot be found."""


@dataclass(frozen=True)
class RelationshipEdge:
    left_table: str
    left_column: str
    right_table: str
    right_column: str
    confidence: float = 0.95
    source: str = "foreign_key_metadata"
    relationship_type: str = "many_to_one"

    def connects(self, table: str) -> bool:
        return self.left_table == table or self.right_table == table

    def other_table(self, table: str) -> str:
        if self.left_table == table:
            return self.right_table
        if self.right_table == table:
            return self.left_table
        raise RelationshipPlanningError(f"Edge does not touch table: {table}")

    def as_join(self) -> Dict[str, Any]:
        return {
            "type": "inner",
            "left_table": self.left_table,
            "left_column": self.left_column,
            "right_table": self.right_table,
            "right_column": self.right_column,
            "confidence": round(self.confidence, 4),
            "source": self.source,
            "relationship_type": self.relationship_type,
        }


@dataclass(frozen=True)
class JoinPath:
    start_table: str
    end_table: str
    edges: Tuple[RelationshipEdge, ...]
    confidence: float
    score: float

    def joins(self) -> List[Dict[str, Any]]:
        return [edge.as_join() for edge in self.edges]


class RelationshipGraphManager:
    def __init__(self, schema_metadata: Optional[dict]):
        self.schema_metadata = schema_metadata or {}
        self.tables = self._load_tables()
        self.edges = self._load_edges()
        self.adjacency = self._build_adjacency()

    def has_relationships(self) -> bool:
        return bool(self.edges)

    def neighbors(self, table: str) -> List[RelationshipEdge]:
        return self.adjacency.get(_identifier(table, "table"), [])

    def _load_tables(self) -> Dict[str, set]:
        tables: Dict[str, set] = {}
        for table in self._iter_tables():
            table_name = _identifier(table.get("table_name"), "table")
            columns = set()
            for column in table.get("columns", []):
                column_name = _first_value(column, ["clean_name", "name", "column_name"])
                if column_name:
                    columns.add(_identifier(column_name, "column"))
            tables[table_name] = columns
        return tables

    def _load_edges(self) -> List[RelationshipEdge]:
        edges: List[RelationshipEdge] = []
        seen = set()

        def add(edge: RelationshipEdge) -> None:
            key = (
                edge.left_table,
                edge.left_column,
                edge.right_table,
                edge.right_column,
            )
            reverse_key = (
                edge.right_table,
                edge.right_column,
                edge.left_table,
                edge.left_column,
            )
            if key in seen or reverse_key in seen:
                return
            self._validate_edge(edge)
            seen.add(key)
            edges.append(edge)

        for relationship in self.schema_metadata.get("relationships", []) or []:
            edge = self._edge_from_relationship(relationship, source="schema_relationship")
            if edge:
                add(edge)

        for table in self._iter_tables():
            table_name = _identifier(table.get("table_name"), "table")
            for relationship in table.get("relationships", []) or []:
                edge = self._edge_from_relationship(
                    relationship,
                    source="table_relationship",
                    default_left_table=table_name,
                )
                if edge:
                    add(edge)

            for column in table.get("columns", []):
                edge = self._edge_from_column_fk(table_name, column)
                if edge:
                    add(edge)

            for foreign_key in table.get("foreign_keys", []) or []:
                edge = self._edge_from_relationship(
                    foreign_key,
                    source="table_foreign_key",
                    default_left_table=table_name,
                )
                if edge:
                    add(edge)

        return edges

    def _iter_tables(self) -> List[Dict[str, Any]]:
        tables = self.schema_metadata.get("tables")
        if isinstance(tables, list):
            return tables
        if all(isinstance(value, dict) for value in self.schema_metadata.values()):
            return [
                {"table_name": table_name, **table_meta}
                for table_name, table_meta in self.schema_metadata.items()
            ]
        return []

    def _build_adjacency(self) -> Dict[str, List[RelationshipEdge]]:
        adjacency: Dict[str, List[RelationshipEdge]] = {table: [] for table in self.tables}
        for edge in self.edges:
            adjacency.setdefault(edge.left_table, []).append(edge)
            adjacency.setdefault(edge.right_table, []).append(edge)
        for table in adjacency:
            adjacency[table].sort(key=lambda edge: (-edge.confidence, edge.left_table, edge.left_column, edge.right_table, edge.right_column))
        return adjacency

    def _edge_from_relationship(
        self,
        relationship: Dict[str, Any],
        source: str,
        default_left_table: Optional[str] = None,
    ) -> Optional[RelationshipEdge]:
        left_table = _first_value(relationship, ["from_table", "source_table", "left_table", "table"]) or default_left_table
        left_column = _first_value(relationship, ["from_column", "source_column", "left_column", "column", "fk_column"])
        right_table = _first_value(relationship, ["to_table", "target_table", "right_table", "ref_table", "referenced_table", "references_table"])
        right_column = _first_value(relationship, ["to_column", "target_column", "right_column", "ref_column", "referenced_column", "references_column"])

        references = relationship.get("references") or relationship.get("fk_ref") or relationship.get("ref")
        if references and (not right_table or not right_column):
            parsed = _parse_ref(references)
            if parsed:
                right_table, right_column = parsed

        if not all([left_table, left_column, right_table, right_column]):
            return None

        return RelationshipEdge(
            left_table=_identifier(left_table, "table"),
            left_column=_identifier(left_column, "column"),
            right_table=_identifier(right_table, "table"),
            right_column=_identifier(right_column, "column"),
            confidence=_coerce_confidence(relationship.get("confidence"), default=0.98),
            source=source,
            relationship_type=relationship.get("relationship_type") or relationship.get("type") or "many_to_one",
        )

    def _edge_from_column_fk(self, table_name: str, column: Dict[str, Any]) -> Optional[RelationshipEdge]:
        fk_ref = column.get("fk_ref") or column.get("references") or column.get("ref")
        is_fk = column.get("is_fk") or column.get("is_foreign_key") or bool(fk_ref)
        if not is_fk:
            return None

        parsed = _parse_ref(fk_ref)
        if not parsed:
            return None

        right_table, right_column = parsed
        left_column = column.get("clean_name") or column.get("name") or column.get("column_name")
        if not left_column:
            return None

        return RelationshipEdge(
            left_table=_identifier(table_name, "table"),
            left_column=_identifier(left_column, "column"),
            right_table=_identifier(right_table, "table"),
            right_column=_identifier(right_column, "column"),
            confidence=_coerce_confidence(column.get("fk_confidence"), default=0.94),
            source="column_foreign_key",
            relationship_type="many_to_one",
        )

    def _validate_edge(self, edge: RelationshipEdge) -> None:
        if edge.left_table not in self.tables:
            raise RelationshipPlanningError(f"Unknown relationship table: {edge.left_table}")
        if edge.right_table not in self.tables:
            raise RelationshipPlanningError(f"Unknown relationship table: {edge.right_table}")
        if edge.left_column not in self.tables[edge.left_table]:
            raise RelationshipPlanningError(f"Unknown relationship column: {edge.left_table}.{edge.left_column}")
        if edge.right_column not in self.tables[edge.right_table]:
            raise RelationshipPlanningError(f"Unknown relationship column: {edge.right_table}.{edge.right_column}")


class JoinConfidenceScorer:
    @staticmethod
    def score_path(edges: Sequence[RelationshipEdge], required_targets: int = 1) -> Tuple[float, float]:
        if not edges:
            return 1.0, 1.0

        avg_edge_confidence = sum(edge.confidence for edge in edges) / len(edges)
        hop_penalty = 1.0 / (1.0 + (max(len(edges) - 1, 0) * 0.12))
        coverage_bonus = min(required_targets / max(len(edges), required_targets), 1.0)
        confidence = _clamp(avg_edge_confidence * hop_penalty)
        score = _clamp((0.80 * confidence) + (0.20 * coverage_bonus))
        return round(confidence, 4), round(score, 4)


class JoinPathPlanner:
    def __init__(self, graph: RelationshipGraphManager):
        self.graph = graph

    def shortest_path(self, start_table: str, end_table: str) -> JoinPath:
        start = _identifier(start_table, "table")
        end = _identifier(end_table, "table")
        if start == end:
            return JoinPath(start, end, tuple(), 1.0, 1.0)
        if start not in self.graph.tables or end not in self.graph.tables:
            raise RelationshipPlanningError(f"Unknown join path endpoint: {start} -> {end}")

        queue: List[Tuple[float, int, str, Tuple[RelationshipEdge, ...]]] = [(0.0, 0, start, tuple())]
        best_cost: Dict[str, float] = {start: 0.0}

        while queue:
            cost, hops, table, path = heapq.heappop(queue)
            if table == end:
                confidence, score = JoinConfidenceScorer.score_path(path)
                return JoinPath(start, end, path, confidence, score)

            if cost > best_cost.get(table, float("inf")):
                continue

            for edge in self.graph.neighbors(table):
                next_table = edge.other_table(table)
                edge_cost = 1.0 + (1.0 - edge.confidence)
                new_cost = cost + edge_cost
                if new_cost < best_cost.get(next_table, float("inf")):
                    best_cost[next_table] = new_cost
                    heapq.heappush(queue, (new_cost, hops + 1, next_table, path + (edge,)))

        raise RelationshipPlanningError(f"No relationship path found: {start} -> {end}")

    def plan_joins(self, source_tables: Sequence[str], preferred_tables: Optional[Sequence[str]] = None) -> Dict[str, Any]:
        required_tables = _dedupe_tables(source_tables)
        if len(required_tables) <= 1:
            return {
                "joins": [],
                "join_paths": [],
                "join_confidence": 1.0,
                "join_score": 1.0,
                "join_strategy": "single_table",
            }

        connected = {required_tables[0]}
        remaining = set(required_tables[1:])
        all_edges: List[RelationshipEdge] = []
        join_paths: List[Dict[str, Any]] = []

        while remaining:
            best: Optional[JoinPath] = None
            best_target: Optional[str] = None
            for start in sorted(connected):
                for target in sorted(remaining):
                    try:
                        candidate = self.shortest_path(start, target)
                    except RelationshipPlanningError:
                        continue
                    if best is None or _path_sort_key(candidate) < _path_sort_key(best):
                        best = candidate
                        best_target = target

            if best is None or best_target is None:
                raise RelationshipPlanningError(
                    f"No relationship path connects required tables: {sorted(connected)} -> {sorted(remaining)}"
                )

            for edge in best.edges:
                if edge not in all_edges:
                    all_edges.append(edge)
                connected.add(edge.left_table)
                connected.add(edge.right_table)
            join_paths.append({
                "start_table": best.start_table,
                "end_table": best.end_table,
                "tables": _tables_in_path(best.start_table, best.edges),
                "hop_count": len(best.edges),
                "confidence": best.confidence,
                "score": best.score,
            })
            remaining.discard(best_target)

        confidence, score = JoinConfidenceScorer.score_path(all_edges, required_targets=max(len(required_tables) - 1, 1))
        return {
            "joins": [edge.as_join() for edge in all_edges],
            "join_paths": join_paths,
            "join_confidence": confidence,
            "join_score": score,
            "join_strategy": "shortest_path",
            "required_tables": required_tables,
            "preferred_tables": _dedupe_tables(preferred_tables or []),
        }


def enrich_logical_plan_with_joins(
    logical_plan: Dict[str, Any],
    schema_metadata: Optional[dict],
    schema_context: Optional[Sequence[dict]] = None,
) -> Dict[str, Any]:
    """Add deterministic join metadata to a logical plan when FK paths exist."""
    if logical_plan.get("is_off_topic"):
        return logical_plan

    source_tables = _plan_source_tables(logical_plan)
    if len(source_tables) <= 1:
        logical_plan.setdefault("joins", [])
        logical_plan.setdefault("join_paths", [])
        logical_plan.setdefault("join_confidence", 1.0)
        logical_plan.setdefault("join_strategy", "single_table")
        return logical_plan

    graph = RelationshipGraphManager(schema_metadata)
    if not graph.has_relationships():
        logical_plan["joins"] = []
        logical_plan["join_paths"] = []
        logical_plan["join_confidence"] = 0.0
        logical_plan["join_strategy"] = "unavailable"
        logical_plan["join_planning_error"] = "No schema foreign-key relationships are available."
        return logical_plan

    preferred_tables = _retrieval_tables(schema_context or [])
    planner = JoinPathPlanner(graph)
    try:
        join_plan = planner.plan_joins(source_tables, preferred_tables=preferred_tables)
    except RelationshipPlanningError as exc:
        logical_plan["joins"] = []
        logical_plan["join_paths"] = []
        logical_plan["join_confidence"] = 0.0
        logical_plan["join_strategy"] = "no_path"
        logical_plan["join_planning_error"] = str(exc)
        return logical_plan

    logical_plan.update(join_plan)
    return logical_plan


async def enrich_logical_plan_with_joins_async(
    logical_plan: Dict[str, Any],
    schema_metadata: Optional[dict],
    schema_context: Optional[Sequence[dict]] = None,
) -> Dict[str, Any]:
    return await asyncio.to_thread(enrich_logical_plan_with_joins, logical_plan, schema_metadata, schema_context)


def _plan_source_tables(logical_plan: Dict[str, Any]) -> List[str]:
    return _dedupe_tables(item.get("table") for item in logical_plan.get("source_tables") or [] if item.get("table"))


def _retrieval_tables(schema_context: Sequence[dict]) -> List[str]:
    return _dedupe_tables(item.get("table") for item in schema_context if item.get("table"))


def _dedupe_tables(tables: Iterable[str]) -> List[str]:
    deduped: List[str] = []
    for table in tables:
        table_name = _identifier(table, "table")
        if table_name not in deduped:
            deduped.append(table_name)
    return deduped


def _path_sort_key(path: JoinPath) -> Tuple[int, float, str]:
    return (len(path.edges), -path.score, path.end_table)


def _tables_in_path(start_table: str, edges: Sequence[RelationshipEdge]) -> List[str]:
    tables = [start_table]
    current = start_table
    for edge in edges:
        current = edge.other_table(current)
        tables.append(current)
    return tables


def _first_value(data: Dict[str, Any], keys: Sequence[str]) -> Optional[Any]:
    for key in keys:
        value = data.get(key)
        if value:
            return value
    return None


def _parse_ref(value: Any) -> Optional[Tuple[str, str]]:
    if not value or not isinstance(value, str):
        return None
    ref = value.strip().lower()
    if "." not in ref:
        return None
    table, column = ref.split(".", 1)
    return _identifier(table, "table"), _identifier(column, "column")


def _identifier(value: Any, kind: str) -> str:
    identifier = str(value or "").strip().lower()
    if not IDENTIFIER_RE.match(identifier):
        raise RelationshipPlanningError(f"Invalid {kind} identifier: {value!r}")
    return identifier


def _coerce_confidence(value: Any, default: float) -> float:
    try:
        return _clamp(float(value))
    except (TypeError, ValueError):
        return default


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return min(max(value, low), high)
