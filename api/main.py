"""FastAPI application — MDM Mapper.

Exposes a single POST /map endpoint that reads a client JSON file from
a local path, runs the mapping pipeline, and returns the Syndigo entity
structure plus a mapping report.

Start the server from the project root:
    python -m uvicorn api.main:app --reload

Then open http://localhost:8000/docs for the interactive Swagger UI.
"""

import json
from pathlib import Path

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from services.client_parser import load_client_json, validate_record
from services.mapper import map_client_to_syndigo

_CONFIG_PATH = Path(__file__).parent.parent / "config" / "mapping_config.yaml"

app = FastAPI(
    title="MDM Mapper",
    description=(
        "Maps a client product JSON file to the Syndigo entity structure. "
        "Supports configurable attribute rules, nested group building, "
        "language detection, and value transforms — all driven by "
        "mapping_config.yaml without code changes."
    ),
    version="1.0.0",
)


# ---------------------------------------------------------------------------
# Config loader — reads fresh from disk on every request so config changes
# take effect without a server restart.
# ---------------------------------------------------------------------------

def _load_config() -> dict:
    with open(_CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class MapRequest(BaseModel):
    input_path: str
    output_path: str | None = None

    model_config = {
        "json_schema_extra": {
            "example": {
                "input_path": r"C:\Users\Ravi\.claude\projects\MDM Mapping\Client JSON.json",
                "output_path": r"C:\Users\Ravi\.claude\projects\MDM Mapping\output_syndigo.json",
            }
        }
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health", tags=["Utility"])
def health() -> dict:
    """Simple liveness check."""
    return {"status": "ok"}


@app.post("/map", tags=["Mapping"])
def map_json(request: MapRequest) -> JSONResponse:
    """Map a client JSON file to Syndigo entity format.

    **input_path** — absolute path to the client JSON file on the server machine.

    **output_path** (optional) — if provided, the result is written to this path
    as a new JSON file.  The endpoint refuses to overwrite an existing file to
    comply with the global don't-overwrite-data-files rule.
    """
    # ── Load input ────────────────────────────────────────────────────────────
    try:
        records = load_client_json(request.input_path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    config = _load_config()

    # ── Map each record ───────────────────────────────────────────────────────
    all_entities: list[dict] = []
    per_record_reports: list[dict] = []
    validation_issues: list[dict] = []

    for idx, record in enumerate(records):
        issues = validate_record(record)
        if issues:
            validation_issues.append({"record_index": idx, "issues": issues})

        entities, report = map_client_to_syndigo(record, config)
        all_entities.extend(entities)
        per_record_reports.append({"record_index": idx, **report})

    # ── Assemble response ─────────────────────────────────────────────────────
    result: dict = {
        "entities": all_entities,
        "_mapping_report": {
            "total_input_records": len(records),
            "total_output_entities": len(all_entities),
            "validation_issues": validation_issues,
            "per_record": per_record_reports,
        },
    }

    # ── Write output file (never overwrite) ───────────────────────────────────
    if request.output_path:
        output_path = Path(request.output_path)
        if output_path.exists():
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Output file already exists: {request.output_path}. "
                    "Provide a new path — existing data files are never overwritten."
                ),
            )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

    return JSONResponse(content=result)
