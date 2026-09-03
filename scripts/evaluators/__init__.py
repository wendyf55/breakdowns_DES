"""Vendored evaluator subsystem.

Copied from the `orchestration` repo (LangGraph Specify-dedup pipeline) so this
project is self-contained — no dependency on that repo's path or install. Only
the classify half is vendored (no search/workflow/merge): the record model, the
pure equality helper, the rule engine, and the LLM evaluator + client + prompts.

Provenance (keep in sync if orchestration's evaluator contract changes):
    base.py, types.py, rule_based.py, llm.py, llm_client.py, prompts.py
        <- orchestration/evaluate/
    equality.py   <- orchestration/utils/equality.py
    record.py     <- orchestration/workflows/types.py (RecordObject)

Adaptation: cross-package imports (`evaluate.*`, `workflows.types`,
`utils.equality`) rewritten to package-local imports. Runtime deps: `requests`
(LLM client) only.
"""

from .base import BaseEvaluator
from .types import MatchPredicate, MatchContext, MatchRule, EvaluationConfig
from .record import RecordObject
from .rule_based import RuleBasedEvaluator
from .llm import LLMEvaluator, LLM_MATCH_KEY
from .llm_client import LLMClient, LLMSettings
from . import prompts

__all__ = [
    "BaseEvaluator", "MatchPredicate", "MatchContext", "MatchRule",
    "EvaluationConfig", "RecordObject", "RuleBasedEvaluator", "LLMEvaluator",
    "LLM_MATCH_KEY", "LLMClient", "LLMSettings", "prompts",
]
