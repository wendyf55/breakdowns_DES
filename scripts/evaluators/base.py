# Vendored from the orchestration repo; imports made package-local. See __init__.py.
# evaluate.base
#
# BaseEvaluator is the abstract interface every evaluation strategy implements.
# The workflow graph's ``evaluate_equality`` node delegates to whichever
# evaluator the concrete workflow selects (via ``evaluator_cls``); the base
# never names a specific strategy — it just calls evaluate().
#
# A concrete evaluator (RuleBasedEvaluator, a future LLMEvaluator, …) owns
# the classification logic: how to compare two records and what classification
# key to assign. It receives the raw candidates plus enough context (column
# spec, table config, and evaluation config) to build comparable objects and
# apply its strategy.

import logging
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class BaseEvaluator(ABC):
    """Abstract base for pluggable evaluation strategies.

    Each strategy implements ``evaluate()``, which takes a flat list of
    candidate records (one seed's worth), the child-row lookup, the resolved
    column spec, and the split table/evaluation configs, and returns a list of
    classified groups — each a ``{"candidates": [...], "classification": str}``
    dict.

    The evaluator is stateless: it is instantiated per call (or once and
    reused), receives everything it needs as arguments, and returns pure data.
    """

    @abstractmethod
    def evaluate(self, candidates, related_by_id, cols, table_config, eval_config):
        """Classify ``candidates`` into groups.

        Args:
            candidates: Raw API records (list-of-lists, id at index 0).
            related_by_id: ``{record_id: {child_table: [rows, …]}}`` lookup
                built by the ``fetch_details`` node.
            cols: The run's resolved ``ColumnSpec`` dict — carries
                ``base_cols`` (the base table's compared column names) and
                ``related_specs`` (the derived child-table structure).
            table_config: The workflow's ``TableConfig`` — carries
                ``match_fields`` and other table-identity config.
            eval_config: The workflow's ``EvaluationConfig`` — carries
                ``match_rules``, ``unmatched_key``.

        Returns:
            A list of dicts, each ``{"candidates": [raw records],
            "classification": str}``.  Every input candidate appears in
            exactly one output group.
        """
        ...
