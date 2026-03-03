"""Client JSON loader and basic validator.

Reads the client input file from disk and normalises it to a list of
product records regardless of whether the file contains a single object
or an array.
"""

import json
from pathlib import Path


def load_client_json(file_path: str) -> list[dict]:
    """Load and parse a client JSON file.

    Accepts either a JSON array of records or a single record object.
    Raises FileNotFoundError / ValueError on bad input.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {file_path}")
    if path.suffix.lower() != ".json":
        raise ValueError(f"Expected a .json file, got suffix '{path.suffix}'")

    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        return data
    raise ValueError(
        "Client JSON must be a JSON array of records or a single record object"
    )


def validate_record(record: dict) -> list[str]:
    """Return a list of validation warnings for a single client record.

    An empty list means the record passed all checks.
    """
    issues: list[str] = []
    data = record.get("data", {})

    if not data:
        issues.append("Record has no 'data' block")
        return issues

    if not data.get("gtin"):
        issues.append("Missing data.gtin — entity ID cannot be fully resolved")
    if not data.get("productnr"):
        issues.append("Missing data.productnr — image linking will be incomplete")
    if not record.get("sources"):
        issues.append(
            "Missing sources[] — miraklshop / artikelnummerbeimlieferanten "
            "cannot be derived"
        )

    return issues
