"""Catalog fixture tests for physics-reference implementation note 9."""
from __future__ import annotations

from esp_engine.catalog import load_catalog


def test_catalog_is_cached_versioned_and_selectable(catalog, fixture_catalog_path):
    again = load_catalog(fixture_catalog_path)
    assert catalog is again
    assert len(catalog.version) == 64
    assert catalog.has_estimated_data is False
    assert catalog.pump("fixture-pump").source_urls
    assert [p.id for p in catalog.pumps_for_casing(5.0, 0.5)] == ["fixture-pump"]
    assert catalog.pumps_for_casing(4.2, 0.1) == []
    assert catalog.pumps_for_rate(500.0)[0].id == "fixture-pump"
    assert catalog.smallest_motor_for_hp(90.0, 400, 4.0).id == "fixture-motor-100"
    assert catalog.smallest_motor_for_hp(250.0, 400, 4.0) is None
    assert catalog.seals_for_series(400)[0].id == "fixture-seal"
    assert catalog.gas_handling_for("gas_handler", 400)[0].id == "fixture-handler"


def test_catalog_unknown_pump_is_actionable(catalog):
    try:
        catalog.pump("missing")
    except ValueError as exc:
        assert "not present" in str(exc)
    else:
        raise AssertionError("missing pump must raise")
