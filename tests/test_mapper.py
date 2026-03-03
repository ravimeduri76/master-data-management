"""Tests for the MDM mapping pipeline.

Run from the project root:
    pytest tests/ -v
"""

from pathlib import Path

import pytest
import yaml

from services.client_parser import load_client_json, validate_record
from services.mapper import map_client_to_syndigo

_CONFIG_PATH = Path(__file__).parent.parent / "config" / "mapping_config.yaml"
_CLIENT_JSON_PATH = Path(
    r"C:/Users/Ravi/.claude/projects/MDM Mapping/Client JSON.json"
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def config() -> dict:
    with open(_CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="module")
def client_record(config) -> dict:
    records = load_client_json(str(_CLIENT_JSON_PATH))
    return records[0]


@pytest.fixture(scope="module")
def mapping_result(client_record, config) -> tuple:
    return map_client_to_syndigo(client_record, config)


@pytest.fixture(scope="module")
def entities(mapping_result) -> list:
    return mapping_result[0]


@pytest.fixture(scope="module")
def report(mapping_result) -> dict:
    return mapping_result[1]


@pytest.fixture(scope="module")
def requestitem(entities) -> dict:
    return next(e for e in entities if e["entity"]["type"] == "requestitem")


@pytest.fixture(scope="module")
def attrs(requestitem) -> dict:
    return requestitem["entity"]["data"]["attributes"]


# ---------------------------------------------------------------------------
# Entity structure
# ---------------------------------------------------------------------------

class TestEntityStructure:
    def test_exactly_one_requestitem(self, entities):
        items = [e for e in entities if e["entity"]["type"] == "requestitem"]
        assert len(items) == 1

    def test_six_image_entities(self, entities):
        images = [e for e in entities if e["entity"]["type"] == "image"]
        assert len(images) == 6

    def test_requestitem_has_id(self, requestitem):
        assert requestitem["entity"]["id"] == "mirakl-4205-4063749440304"

    def test_requestitem_has_src(self, requestitem):
        assert requestitem["entity"]["properties"]["src"] == "mkl"


# ---------------------------------------------------------------------------
# Simple attribute mapping
# ---------------------------------------------------------------------------

class TestSimpleAttributes:
    def test_gtin_scalar(self, attrs):
        assert attrs["gtin"]["values"][0]["value"] == "4063749440304"

    def test_genericbrand(self, attrs):
        assert attrs["genericbrand"]["values"][0]["value"] == "Olsen"

    def test_genericarticletitle(self, attrs):
        val = attrs["genericarticletitle"]["values"][0]["value"]
        assert "Pullover" in val

    def test_multivalue_season(self, attrs):
        values = [v["value"] for v in attrs["genericseason"]["values"]]
        assert "Herbst" in values
        assert "Winter" in values

    def test_locale_present_on_values(self, attrs):
        assert "locale" in attrs["genericbrand"]["values"][0]


# ---------------------------------------------------------------------------
# Value transforms
# ---------------------------------------------------------------------------

class TestValueTransforms:
    def test_steuer_transform(self, attrs):
        assert attrs["steuerklassifikationartikel"]["values"][0]["value"] == "Voll (19%)"

    def test_nontextile_transform(self, attrs):
        assert attrs["nontextilepartsofanimalorigin"]["values"][0]["value"] == "False"


# ---------------------------------------------------------------------------
# Numbered groups
# ---------------------------------------------------------------------------

class TestNumberedGroups:
    def test_material_group_exists(self, attrs):
        assert "genericmaterialfashion" in attrs

    def test_material_group_has_three_entries(self, attrs):
        groups = attrs["genericmaterialfashion"]["group"]
        assert len(groups) == 3

    def test_material_values_correct(self, attrs):
        groups = attrs["genericmaterialfashion"]["group"]
        materials = [g["materialfashionmaterial"]["values"][0]["value"] for g in groups]
        assert materials == ["Viskose", "Polyester", "Polyamid"]

    def test_material_parts_correct(self, attrs):
        groups = attrs["genericmaterialfashion"]["group"]
        parts = [g["materialfashionparts"]["values"][0]["value"] for g in groups]
        assert parts == ["52", "28", "20"]

    def test_material_location_default_injected(self, attrs):
        groups = attrs["genericmaterialfashion"]["group"]
        for g in groups:
            assert g["materialfashionlocation"]["values"][0]["value"] == "Gesamt"

    def test_noticeable_features_group_exists(self, attrs):
        assert "genericnoticeablefeatures" in attrs

    def test_noticeable_features_five_entries(self, attrs):
        groups = attrs["genericnoticeablefeatures"]["group"]
        assert len(groups) == 5

    def test_noticeable_features_values(self, attrs):
        groups = attrs["genericnoticeablefeatures"]["group"]
        first = groups[0]["noticeablefeaturetext"]["values"][0]["value"]
        assert "Olsen" in first


# ---------------------------------------------------------------------------
# Single groups
# ---------------------------------------------------------------------------

class TestSingleGroups:
    def test_care_label_group_exists(self, attrs):
        assert "genericcarelabeltextile" in attrs

    def test_care_label_single_entry(self, attrs):
        assert len(attrs["genericcarelabeltextile"]["group"]) == 1

    def test_care_label_sub_attributes_present(self, attrs):
        entry = attrs["genericcarelabeltextile"]["group"][0]
        for key in [
            "carelabeltextileironing",
            "carelabeltextilewashing",
            "carelabeltextiledrycleaning",
            "carelabeltextiledrying",
            "carelabeltextilebleaching",
        ]:
            assert key in entry, f"Missing care label sub-key: {key}"

    def test_target_group_exists(self, attrs):
        assert "generictargetgroup" in attrs

    def test_target_group_value(self, attrs):
        val = attrs["generictargetgroup"]["group"][0]["targetgroupgeneral"]["values"][0]["value"]
        assert val == "Damen"


# ---------------------------------------------------------------------------
# Derived attributes
# ---------------------------------------------------------------------------

class TestDerivedAttributes:
    def test_galmklid_from_productnr(self, attrs):
        assert attrs["galmklid"]["values"][0]["value"] == "b6346658-66c5-4adb-89a6-daad73e05cab"

    def test_miraklshop_from_sources(self, attrs):
        assert attrs["miraklshop"]["values"][0]["value"] == "4205"

    def test_datumgueltigab_date_only(self, attrs):
        val = attrs["datumgueltigab"]["values"][0]["value"]
        assert val == "2026-01-07"
        assert "T" not in val

    def test_galeriataxonomy_from_productcategory(self, attrs):
        assert attrs["galeriataxonomy"]["values"][0]["value"] == "womenspullovers"

    def test_sysmdmid_template(self, attrs):
        assert attrs["sysmdmid"]["values"][0]["value"] == "mirakl-4205-4063749440304"

    def test_sourcecomment_is_iso_timestamp(self, attrs):
        val = attrs["sourcecomment"]["values"][0]["value"]
        assert "T" in val  # ISO datetime format


# ---------------------------------------------------------------------------
# Image entities
# ---------------------------------------------------------------------------

class TestImageEntities:
    def test_image_entity_type(self, entities):
        images = [e for e in entities if e["entity"]["type"] == "image"]
        for img in images:
            assert img["entity"]["type"] == "image"

    def test_main_image_media_type(self, entities):
        images = [e for e in entities if e["entity"]["type"] == "image"]
        main = next(
            e for e in images
            if e["entity"]["data"]["attributes"]["dammediaid"]["values"][0]["value"] == "01"
        )
        assert (
            main["entity"]["data"]["attributes"]["dammediatype"]["values"][0]["value"]
            == "MainImage"
        )

    def test_additional_image_media_type(self, entities):
        images = [e for e in entities if e["entity"]["type"] == "image"]
        additional = [
            e for e in images
            if e["entity"]["data"]["attributes"]["dammediaid"]["values"][0]["value"] != "01"
        ]
        for img in additional:
            assert (
                img["entity"]["data"]["attributes"]["dammediatype"]["values"][0]["value"]
                == "AdditionalImage"
            )

    def test_image_checksum_is_md5(self, entities):
        images = [e for e in entities if e["entity"]["type"] == "image"]
        for img in images:
            checksum = img["entity"]["id"]
            assert len(checksum) == 32
            assert checksum.isalnum()

    def test_image_productnr_linked(self, entities):
        images = [e for e in entities if e["entity"]["type"] == "image"]
        for img in images:
            val = img["entity"]["data"]["attributes"]["productnr"]["values"][0]["value"]
            assert val == "b6346658-66c5-4adb-89a6-daad73e05cab"


# ---------------------------------------------------------------------------
# Mapping report
# ---------------------------------------------------------------------------

class TestMappingReport:
    def test_report_has_required_keys(self, report):
        for key in [
            "unmapped_client_keys",
            "system_fields_omitted",
            "language_detection_warnings",
            "transform_warnings",
            "derived_warnings",
            "image_warnings",
        ]:
            assert key in report

    def test_no_unmapped_client_keys(self, report):
        assert report["unmapped_client_keys"] == [], (
            f"Unexpected unmapped keys: {report['unmapped_client_keys']}"
        )

    def test_thingrunpath_reported_as_omitted(self, report):
        assert "thingRunPath" in report["system_fields_omitted"]

    def test_original_url_reported_in_image_warnings(self, report):
        assert any("original_url" in w for w in report["image_warnings"])


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

class TestValidation:
    def test_valid_record_no_issues(self, client_record):
        issues = validate_record(client_record)
        assert issues == []
