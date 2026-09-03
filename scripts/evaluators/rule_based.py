# Vendored from the orchestration repo; imports made package-local. See __init__.py.
# evaluate.rule_based
#
# RuleBasedEvaluator implements the ordered first-match-wins predicate engine
# that was previously inlined in BaseWorkflowBuilder.evaluate_equality. It
# applies the workflow's match_rules in priority order: for each candidate,
# the first rule whose predicate fires wins, and the matched records form a
# group under that rule's key. A candidate no rule matched becomes its own
# group under the spec's unmatched_key.
#
# This is the default evaluator for every current workflow (locality,
# geography, agent, taxon). Future evaluators (LLM-based, statistical, …)
# implement the same BaseEvaluator.evaluate() interface with different
# classification logic.

import logging
from functools import partial

from .base import BaseEvaluator
from .types import MatchContext
from .record import RecordObject
from .equality import get_matches

logger = logging.getLogger(__name__)


class RuleBasedEvaluator(BaseEvaluator):
    """First-match-wins ordered-predicate evaluator.

    Applies ``spec.match_rules`` in priority order. Each rule carries a
    predicate ``(candidate, other, context) -> bool``; the first rule to
    match at least one other record wins the group. Records no rule matched
    fall to ``spec.unmatched_key``.

    Stateless: everything needed is passed into ``evaluate()``.
    """

    def evaluate(self, candidates, related_by_id, cols, table_config, eval_config):
        base_cols = cols["base_cols"]
        context = MatchContext(tuple(base_cols), cols["related_specs"], table_config.match_fields)
        parse = lambda record, related: RecordObject.parse(record, base_cols, related)

        groups = []
        remaining = candidates[:]
        while remaining:
            candidate = remaining.pop(0)
            for rule in eval_config.match_rules:
                predicate = partial(rule.predicate, context=context)
                matched, leftover = get_matches(
                    candidate, remaining, related_by_id, predicate, parse,
                )
                if matched:
                    logger.debug(
                        "%s group ids: %s",
                        rule.key,
                        [r[0] for r in (candidate, *matched)],
                    )
                    groups.append({
                        "candidates": [candidate, *matched],
                        "classification": str(rule.key),
                    })
                    remaining = leftover
                    break
            else:
                logger.debug("%s: %s", eval_config.unmatched_key, candidate[0])
                groups.append({
                    "candidates": [candidate],
                    "classification": str(eval_config.unmatched_key),
                })

        return groups
