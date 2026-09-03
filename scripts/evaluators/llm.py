# Vendored from the orchestration repo; imports made package-local. See __init__.py.
# evaluate.llm
#
# LLMEvaluator — a whole-group LLM-based evaluation strategy that implements
# the same BaseEvaluator.evaluate() interface as RuleBasedEvaluator.
#
# Instead of applying pairwise predicate rules, it sends the full candidate
# group (already enriched with child data) to an LLM and asks it to cluster
# the records into duplicate groups. The LLM returns structured JSON with
# groups of record ids; the evaluator maps that back to the standard
# [{"candidates": [...], "classification": str}] shape that the rest of the
# pipeline expects.
#
# The evaluator is table-agnostic: the prompt is built from the table config
# and column spec the workflow already provides.

import logging

from .base import BaseEvaluator
from .llm_client import LLMClient
from .prompts import build_prompt

logger = logging.getLogger(__name__)

# Classification key for groups the LLM identifies as duplicates.
LLM_MATCH_KEY = "llm_match"


class LLMEvaluator(BaseEvaluator):
    """Whole-group LLM-based duplicate evaluator.

    Sends all candidates to an LLM in one call, receives cluster assignments,
    and returns classified groups in the same shape as RuleBasedEvaluator.
    """

    def __init__(self, client: LLMClient | None = None):
        if client is None:
            client = LLMClient.from_env()
        self.client = client

    def evaluate(self, candidates, related_by_id, cols, table_config, eval_config):
        """Classify candidates by asking the LLM to cluster them.

        Falls back to marking everything as unmatched if the LLM call fails
        or returns unparseable output — fail closed, same as the rule-based
        evaluator's no-match path.
        """
        if len(candidates) < 2:
            return [{
                "candidates": candidates,
                "classification": eval_config.unmatched_key,
            }]

        system, user = build_prompt(candidates, related_by_id, cols, table_config)

        try:
            result = self.client.query(system=system, user=user)
            return self._parse_response(result, candidates, eval_config)
        except Exception:
            logger.exception("LLM evaluation failed; falling back to unmatched")
            return [{
                "candidates": [c],
                "classification": eval_config.unmatched_key,
            } for c in candidates]

    def _parse_response(self, result, candidates, eval_config):
        """Map the LLM's JSON response to the standard group format.

        Expected response shape:
            {
                "groups": [{"ids": [1, 2], "reason": "..."}, ...],
                "unmatched": [3, 4]
            }
        """
        # Build an id -> raw record lookup for fast access.
        by_id = {record[0]: record for record in candidates}
        all_ids = set(by_id.keys())
        claimed = set()
        groups = []

        for group in result.get("groups", []):
            ids = group.get("ids", [])
            reason = group.get("reason", "")
            # Validate: skip groups with unknown ids or fewer than 2 members.
            valid_ids = [i for i in ids if i in all_ids and i not in claimed]
            if len(valid_ids) < 2:
                logger.debug("Skipping LLM group with <2 valid ids: %s", ids)
                continue
            claimed.update(valid_ids)
            logger.info(
                "%s group ids: %s — %s", LLM_MATCH_KEY, valid_ids, reason,
            )
            groups.append({
                "candidates": [by_id[i] for i in valid_ids],
                "classification": LLM_MATCH_KEY,
            })

        # Everything the LLM didn't cluster (including its explicit "unmatched"
        # list, plus any ids it forgot to mention) goes to unmatched.
        unclaimed = all_ids - claimed
        for record_id in unclaimed:
            groups.append({
                "candidates": [by_id[record_id]],
                "classification": eval_config.unmatched_key,
            })

        return groups
