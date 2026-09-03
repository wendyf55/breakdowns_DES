"""Shared configuration loaded from .env in the project root."""

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


def _csv_list(key: str) -> list[str]:
    val = os.getenv(key, "")
    return [v.strip() for v in val.split(",") if v.strip()]


# Specify connection
BASE_URL = os.getenv("SPECIFY_BASE_URL", "")
USERNAME = os.getenv("SPECIFY_USERNAME", "")
PASSWORD = os.getenv("SPECIFY_PASSWORD", "")
COLLECTION_ID = os.getenv("SPECIFY_COLLECTION_ID", "")

# Filters — case-insensitive substring patterns
FILTER_COLLECTORS = _csv_list("FILTER_COLLECTORS")
FILTER_LOCALITY = _csv_list("FILTER_LOCALITY")

# Mushroom Observer discovery seeds (README §2): the Ceska MO account
# and the Observatory Hill location. Used by get_mo_records.py.
MO_USER = os.getenv("MO_USER", "")
MO_LOCATION = os.getenv("MO_LOCATION", "")

# Directories
DATA_DIR = PROJECT_ROOT / "data"
REPORTS_DIR = PROJECT_ROOT / "reports"

