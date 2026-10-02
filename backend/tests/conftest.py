from __future__ import annotations

from pathlib import Path
import pytest

from esp_engine.catalog import Catalog, load_catalog


@pytest.fixture(scope="session")
def fixture_catalog_path() -> Path:
    return Path(__file__).parent / "fixtures" / "catalog"


@pytest.fixture
def catalog(fixture_catalog_path: Path) -> Catalog:
    return load_catalog(fixture_catalog_path)
