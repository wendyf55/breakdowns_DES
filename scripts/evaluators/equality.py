# Vendored from the orchestration repo; imports made package-local. See __init__.py.
"""Pure record-equality helpers (table-agnostic; used by every workflow).

These functions decide whether two records count as "the same". They are
deliberately free of any project or runtime imports (standard library only):
every domain specific — which fields to compare, which child tables count, the
case-insensitive name field, and how to turn a raw record into a comparable
object — is passed in by the caller. That keeps the matching logic isolated and
unit-testable on its own, without the graph/runtime stack.
"""

import json

def multiset_key(rows):
    """Sort child rows into a canonical order for multiset comparison.

    Returns the actual row dicts in a stable order (sorted by their canonical
    JSON form) so callers compare with native ``==`` — deep value equality —
    rather than string equality.

    Note: if ``default=str`` maps two genuinely different values to the same
    string, the JSON sort key ties and ``sorted`` falls back on input order
    (stable sort). Two lists with the same rows in different input orders
    could then sort differently, producing a false negative. In practice
    this does not arise because child rows come from the same Specify API
    serialization path, but it is a theoretical edge case.

    Args:
        rows: A record's child rows for one table, with the row id already
            stripped.

    Returns:
        A sorted list of row dicts, usable for ``==`` multiset comparison.
    """
    return sorted(rows, key=lambda row: json.dumps(row, sort_keys=True, default=str))


def related_match(a_related, b_related, related_specs):
    """Test whether two records' related child rows are equal.

    Compares the two records table by table over ``related_specs``. For each
    table, the child rows must match as an unordered multiset (order ignored,
    duplicates respected); a table missing from either side is treated as an
    empty list. Returns True only if every table matches.

    Args:
        a_related: First record's child rows, keyed by table name.
        b_related: Second record's child rows, keyed by table name.
        related_specs: Iterable of table names to compare (e.g. the keys of the
            caller's related-table spec mapping).

    Returns:
        True if all related tables match for both records, else False.
    """
    return all(
        multiset_key(a_related.get(table, [])) == multiset_key(b_related.get(table, []))
        for table in related_specs
    )


def fields_equal(a, b, fields):
    """True when ``a`` and ``b`` agree on every attribute named in ``fields``."""
    return all(getattr(a, f) == getattr(b, f) for f in fields)


def casefold_equal(left, right):
    """Case-insensitive equality with an explicit null policy.

    Two nulls are equal; a null and a non-null differ; otherwise the values are
    compared by their Unicode case-folded forms (``str.casefold`` — stricter than
    ``.lower()`` for non-ASCII).
    """
    if left is None or right is None:
        return left is right
    return str(left).casefold() == str(right).casefold()


##############################################################################
# Context-based predicates (the generic BaseWorkflowBuilder interface)

def exact_rule(candidate, other, context):
    """Every compared field equal and all child rows equal (an exact duplicate)."""
    return (fields_equal(candidate, other, context.compare_fields)
            and related_match(candidate.related, other.related, context.related_specs))


def case_rule(candidate, other, context):
    """Name fields equal case-insensitively; every other field and child row exact."""
    name_fields = context.match_fields
    other_fields = [field for field in context.compare_fields if field not in name_fields]
    return (fields_equal(candidate, other, other_fields)
            and all(casefold_equal(getattr(candidate, field), getattr(other, field))
                    for field in name_fields)
            and related_match(candidate.related, other.related, context.related_specs))


def get_matches(candidate_record, other_records, related_by_id, is_match, parse):
    """Split ``other_records`` into those matching ``candidate_record`` and the rest.

    Args:
        candidate_record: The raw record everything else is compared against.
        other_records: Remaining raw records to test.
        related_by_id: Map of record-id -> child rows, used when building the
            comparable objects.
        is_match: Predicate ``(candidate_obj, other_obj) -> bool``, already bound
            to its ``compare_fields``/``related_specs`` (e.g. via
            ``functools.partial``).
        parse: Factory ``(record, related) -> object`` turning a raw record plus
            its related rows into a comparable object (e.g.
            ``LocationObject.parse``).

    Returns:
        A ``(matches, leftovers)`` tuple of the original raw records.
    """
    candidate = parse(candidate_record, related_by_id.get(candidate_record[0]))

    matches = []
    leftovers = []

    for record in other_records:
        other = parse(record, related_by_id.get(record[0]))

        if is_match(candidate, other):
            matches.append(record)
        else:
            leftovers.append(record)

    return matches, leftovers


def fuzzy_rule(candidate, other, context):
    """Non-name fields and child rows equal; names ignored (search validated similarity).

    Sits below ``exact_rule`` and ``case_rule`` in the rule cascade: by the time
    this fires, names already failed both exact and case-insensitive comparison.
    The fuzzy search builder guarantees the candidates' names are similar enough
    to be worth comparing — so this predicate only checks whether everything
    *else* agrees, without re-imposing a second similarity threshold on the names.
    """
    name_fields = context.match_fields
    other_fields = [field for field in context.compare_fields if field not in name_fields]
    return (fields_equal(candidate, other, other_fields)
            and related_match(candidate.related, other.related, context.related_specs))
