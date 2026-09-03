# Vendored from the orchestration repo; imports made package-local. See __init__.py.
# evaluate.prompts
#
# Prompt templates for the LLM evaluator. The generic template works for any
# table; table-specific hints can be added via DOMAIN_HINTS without changing
# the evaluator or the prompt structure.

# Table-specific context that helps the LLM understand the domain. Keys are
# table names; values are appended to the system prompt. Add new tables here
# as needed — a missing table just gets the generic prompt.
DOMAIN_HINTS = {
    "agent": (
        "These are person/organization records. Name differences may include "
        "transposed first/last names, initials vs full names, nicknames, or "
        "punctuation/diacritics. Two agents with different institutional "
        "affiliations or roles are likely distinct even if names are similar."
    ),
    "locality": (
        "These are collecting-locality records (geographic locations where "
        "specimens were collected). Two localities with the same name but "
        "different coordinates, elevation, or discipline are likely distinct."
    ),
    "geography": (
        "These are geography-tree nodes (administrative divisions: countries, "
        "states, counties). Nodes under different parents or in different "
        "trees (definitions) are distinct even if they share a name."
    ),
    "taxon": (
        "These are taxon-tree nodes (biological taxonomy). Nodes under "
        "different parents, at different ranks, or in different trees "
        "(definitions) are distinct even if they share a name."
    ),
}


SYSTEM_TEMPLATE = """\
You are a duplicate-record detector for a natural history collections database.

You will receive a group of {count} records from the "{table}" table that share \
the same value on their name field ({match_fields}). Your job is to decide which \
records, if any, are accidental duplicates of each other — records that were \
entered more than once and should be merged into one.

{domain_hint}

RULES:
- Two records are duplicates ONLY when they clearly refer to the same real-world \
entity and differ only by accident (case, whitespace, missing optional fields, \
minor formatting).
- Records that differ on any substantive field (a different parent, definition, \
discipline, coordinates, or meaningful data) are NOT duplicates.
- When in doubt, say they are NOT duplicates. A missed merge just leaves a \
duplicate; a false merge destroys a real record.
- A group may contain multiple independent clusters of duplicates, or none at all.

Return a JSON object with exactly this structure:
{{
  "groups": [
    {{"ids": [<id>, <id>, ...], "reason": "<one-sentence explanation>"}},
    ...
  ],
  "unmatched": [<id>, ...]
}}

Every record id must appear in exactly one group or in unmatched. A group must \
have at least 2 ids. If no records are duplicates, return an empty groups array \
and put all ids in unmatched.\
"""


def build_record_block(record_id, fields, values, child_summary):
    """Render one record as a readable block for the prompt."""
    lines = [f"Record (id {record_id}):"]
    for field, value in zip(fields, values):
        lines.append(f"  {field}: {value}")
    if child_summary:
        for table_name, rows in child_summary.items():
            count = len(rows)
            lines.append(f"  [{table_name}]: {count} child row{'s' if count != 1 else ''}")
            # Show the first few child rows for context (keep it compact)
            for row in rows[:3]:
                lines.append(f"    {row}")
            if count > 3:
                lines.append(f"    ... and {count - 3} more")
    return "\n".join(lines)


def build_prompt(candidates, related_by_id, cols, table_config):
    """Build the system and user prompts from the evaluation inputs.

    Args:
        candidates: Raw API records (list-of-lists, id at index 0).
        related_by_id: {record_id: {child_table: [rows, ...]}} lookup.
        cols: The run's resolved ColumnSpec dict.
        table_config: The workflow's TableConfig.

    Returns:
        (system_prompt, user_prompt) tuple of strings.
    """
    table = table_config.table
    match_fields = ", ".join(table_config.match_fields)
    domain_hint = DOMAIN_HINTS.get(table, "")
    base_cols = cols["base_cols"]

    system = SYSTEM_TEMPLATE.format(
        count=len(candidates),
        table=table,
        match_fields=match_fields,
        domain_hint=domain_hint,
    )

    record_blocks = []
    for record in candidates:
        record_id = record[0]
        values = record[1:]  # id is at index 0, then base_cols in order
        child_data = related_by_id.get(record_id, {})
        block = build_record_block(record_id, base_cols, values, child_data)
        record_blocks.append(block)

    user = "\n\n".join(record_blocks)
    return system, user
