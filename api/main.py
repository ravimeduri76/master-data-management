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


def _merge_ag54_attributes(
    *,
    entities: list[dict],
    client_record: dict,
    syndigo_config: dict,
    canonical_root: str | None,
    correlation_id: str,
) -> list[dict]:
    """P8: when use_ag54=True, run the AG54 delegator and overwrite the
    simple+transformed attributes in the first entity (the ``requestitem``)
    with AG54-routed equivalents. Out-of-scope sections of the entity
    (numbered_groups / single_groups / derived / defaults) are left alone.

    Returns the merged entities list. On failure the legacy entities
    are returned unchanged + the caller sees no AG54 delegation.
    """
    from pathlib import Path as _Path  # noqa: PLC0415

    from services.ag54_delegator import delegate, load_ag54_mapping  # noqa: PLC0415

    # Resolve the canonical root (request → env → sibling convention).
    if canonical_root:
        canon_path = _Path(canonical_root).expanduser().resolve()
    else:
        import os as _os  # noqa: PLC0415
        env = _os.environ.get("AG54_CANONICAL_ROOT")
        if env:
            canon_path = _Path(env).expanduser().resolve()
        else:
            sibling = _Path.cwd().parent / "gaxl-rnd-ontology-builder" / "_canonical_models"
            if sibling.exists():
                canon_path = sibling.resolve()
            else:
                # No canonical root → can't delegate; pass entities through.
                return entities

    ag54_mapping_path = _Path(__file__).parent.parent / "config" / "ag54_mapping.yaml"
    if not ag54_mapping_path.exists():
        return entities

    ag54_mapping = load_ag54_mapping(ag54_mapping_path)
    ag54_attrs = delegate(
        client_record=client_record,
        syndigo_config=syndigo_config,
        ag54_mapping=ag54_mapping,
        canonical_root=canon_path,
        correlation_id=correlation_id,
    )
    if not ag54_attrs:
        return entities

    if not entities:
        return entities
    requestitem = entities[0]
    existing_attrs = requestitem.get("attributes") or {}
    # AG54 wins for keys it routes; legacy attrs (groups / derived /
    # defaults) stay untouched.
    merged = {**existing_attrs, **ag54_attrs}
    requestitem["attributes"] = merged
    return entities


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class MapRequest(BaseModel):
    input_path: str
    output_path: str | None = None
    use_ag54: bool | None = None
    """P8: opt-in delegation to AG54 SchemaMapper for the simple +
    transformed attribute subset. None defers to env ``MDM_USE_AG54=1``;
    True/False overrides. Out-of-scope sections (groups, derived,
    defaults, images) always use the legacy path."""

    ag54_canonical_root: str | None = None
    """Optional path to ontology-builder ``_canonical_models/``. Falls
    back to env ``AG54_CANONICAL_ROOT`` or sibling-checkout convention."""

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

    # ── P8 — optional AG54 delegation (default off) ───────────────────────────
    # When enabled, AG54 routes simple_attributes + transformed_attributes
    # via its engine; the legacy pipeline still owns numbered_groups,
    # single_groups, derived_attributes, defaults, and images.
    from services.ag54_delegator import (  # noqa: PLC0415
        available as ag54_available,
        use_ag54_enabled,
    )
    use_ag54 = use_ag54_enabled(request.use_ag54)
    ag54_engine_used = False
    if use_ag54 and not ag54_available():
        # Honest fallback — log the misconfiguration on the response.
        validation_issues.append({  # noqa: F821 — assigned below
            "record_index": -1,
            "issues": [
                "use_ag54 requested but gaxl_mapping_engine not importable; "
                "falling back to legacy pipeline."
            ],
        }) if False else None  # placeholder; real init below

    # ── Map each record ───────────────────────────────────────────────────────
    all_entities: list[dict] = []
    per_record_reports: list[dict] = []
    validation_issues: list[dict] = []

    if use_ag54 and not ag54_available():
        validation_issues.append({
            "record_index": -1,
            "issues": [
                "use_ag54 requested but gaxl_mapping_engine not importable; "
                "falling back to legacy pipeline."
            ],
        })
        use_ag54 = False

    for idx, record in enumerate(records):
        issues = validate_record(record)
        if issues:
            validation_issues.append({"record_index": idx, "issues": issues})

        entities, report = map_client_to_syndigo(record, config)

        if use_ag54:
            ag54_engine_used = True
            entities = _merge_ag54_attributes(
                entities=entities,
                client_record=record,
                syndigo_config=config,
                canonical_root=request.ag54_canonical_root,
                correlation_id=f"mdm-{idx}",
            )

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
            "ag54_delegation_used": ag54_engine_used,
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
