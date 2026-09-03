# Vendored from orchestration/workflows/types.py (the RecordObject class only).
# A table-agnostic parsed record: comparison fields become named attributes so
# predicates can read them as attributes.

class RecordObject:
    """A parsed record with its comparison fields as named attributes.

    ``compare_fields`` is supplied per object (resolved at runtime), so the same
    class serves every use — here, cross-platform BBM/MO specimen records.
    """
    id: int

    def __init__(self, data: dict, compare_fields):
        self.compare_fields = compare_fields
        for field_name in ("id", *compare_fields):
            setattr(self, field_name, data[field_name])
        self.related = {}

    @classmethod
    def parse(cls, record: list, compare_fields, related: dict = None) -> "RecordObject":
        """Construct a RecordObject from a raw record (positional: id first)."""
        fields = ("id", *compare_fields)
        obj = cls(dict(zip(fields, record)), compare_fields)
        obj.related = related or {}
        return obj
