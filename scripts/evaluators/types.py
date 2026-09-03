# Vendored from the orchestration repo; imports made package-local. See __init__.py.
# evaluate.types
#
# Evaluation-owned type definitions, extracted from builders.workflow_base.
# These are the classification/matching types: predicates, match rules,
# match context, and evaluation configuration.

from dataclasses import dataclass
from typing import Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from .record import RecordObject


##############################################################################
# Predicate type
#
# A predicate is any pure function (candidate, other, context) -> bool. The
# evaluator applies it without knowing its domain logic; scores/thresholds
# stay private to the predicate.
MatchPredicate = Callable[["RecordObject", "RecordObject", "MatchContext"], bool]


##############################################################################
# Match context (what every predicate receives alongside the two records)
#
@dataclass(frozen=True)
class MatchContext:
    """What every predicate receives alongside the two records to compare."""
    compare_fields: tuple
    related_specs: dict
    match_fields: tuple


##############################################################################
# Match rule
#
@dataclass(frozen=True)
class MatchRule:
    """One ordered equality rule: its workflow-owned classification key, its
    predicate, and whether groups with this key are surfaced for review."""
    key: str
    predicate: MatchPredicate
    reviewable: bool = True


##############################################################################
# Evaluation configuration (the method half of the old WorkflowSpec)
#
@dataclass(frozen=True)
class EvaluationConfig:
    """Configuration for the evaluation step — which rules to apply and
    what to call unmatched records.
 
    This is the evaluation-method half of what was previously WorkflowSpec.
    It carries no table-identity config; that lives in
    workflows.types.TableConfig.
 
    ``compare_cols`` (optional): when set, the evaluator compares only these
    columns instead of the full resolved ``base_cols``. The full column set
    is still fetched and displayed — this controls only what the predicates
    see. Used by ``SubsetColumnsEvaluator`` to relax matching
    (e.g. geography cross-parent dedup).
 
    ``evaluator_cls`` (optional): the evaluator class to use for this
    config. When set, the workflow uses this instead of its class-level
    default. This lets each strategy bundle its own evaluator.
    """
    match_rules: tuple
    unmatched_key: str
    compare_cols: tuple | None = None
    evaluator_cls: type | None = None
