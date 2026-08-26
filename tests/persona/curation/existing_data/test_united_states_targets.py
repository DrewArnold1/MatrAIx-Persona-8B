"""Invariants for the US adult calibration targets and the raking front-end.

The failure mode these guard against is silent: rake_weights divides by the sum
of the codes supplied while counting every observed row in the denominator, so a
mistyped label or a dropped category does not raise - it quietly returns weights
that calibrate to the wrong distribution.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
TARGETS_PATH = REPO_ROOT / "persona/curation/existing_data/united_states/targets_us.json"
SCRIPT_PATH = REPO_ROOT / "persona/curation/existing_data/united_states/rake_us_adults.py"
SCHEMA_PATH = REPO_ROOT / "persona/schema/dimensions.json"
DEV_POOL = REPO_ROOT / "persona/datasets/matraix-persona-dev-sample"

CHILD_AGE_VALUES = {"Under 5", "5-12", "13-17"}


def _load_module():
    spec = importlib.util.spec_from_file_location("rake_us_adults", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def rake():
    return _load_module()


@pytest.fixture(scope="module")
def targets():
    return json.loads(TARGETS_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def schema_values(rake):
    return rake.load_schema_values(SCHEMA_PATH)


def test_every_share_label_exists_in_the_schema(targets, schema_values):
    """A typo here becomes a KeyError at load time, or worse, a skewed margin."""
    for dimension, definition in targets["dimensions"].items():
        assert dimension in schema_values, f"unknown dimension {dimension}"
        allowed = set(schema_values[dimension])
        unknown = sorted(set(definition.get("shares") or {}) - allowed)
        assert not unknown, f"{dimension} uses non-schema labels {unknown}"


def test_populated_margins_sum_to_one(targets):
    for dimension, definition in targets["dimensions"].items():
        shares = definition.get("shares") or {}
        if not shares:
            continue
        total = sum(shares.values())
        assert total == pytest.approx(1.0, abs=5e-4), f"{dimension} sums to {total}"


def test_shares_are_non_negative(targets):
    for dimension, definition in targets["dimensions"].items():
        for label, share in (definition.get("shares") or {}).items():
            assert share >= 0, f"{dimension}/{label} is negative"


def test_default_rake_dimensions_are_usable(targets):
    for dimension in targets["default_rake_dimensions"]:
        definition = targets["dimensions"].get(dimension)
        assert definition is not None, f"{dimension} missing from dimensions"
        assert definition.get("shares"), f"{dimension} is in the defaults but empty"


def test_age_target_excludes_minors(targets):
    """The target set is adults-only; a child bracket here would silently skew."""
    shares = targets["dimensions"]["age_bracket"]["shares"]
    assert not CHILD_AGE_VALUES.intersection(shares)


def test_socioeconomic_band_stays_deliberately_empty(targets):
    """Household finances are the dependent variable - raking on them bakes in
    the finding. This emptiness is load-bearing, not an oversight."""
    definition = targets["dimensions"]["socioeconomic_band"]
    assert definition["shares"] == {}
    assert definition["weight"] == 0.0


def test_targets_declare_unverified_provenance(targets):
    """These shares were hand-authored offline. If someone later regenerates them
    from primary sources they should have to change this flag deliberately."""
    assert targets["status"] == "draft_unverified"
    assert "UNVERIFIED" in targets["provenance"]["verification_status"]


def test_selection_drops_minors_and_other_regions(rake):
    personas = [
        {"persona_id": "a", "dimensions": {"region": "North America", "age_bracket": "35-44"}},
        {"persona_id": "b", "dimensions": {"region": "North America", "age_bracket": "5-12"}},
        {"persona_id": "c", "dimensions": {"region": "East Asia", "age_bracket": "35-44"}},
        {"persona_id": "d", "dimensions": {"age_bracket": "35-44"}},
        {"persona_id": "e", "dimensions": {"region": "North America"}},
    ]
    kept, audit = rake.select_us_adults(personas, require_us_culture=False)
    assert [p["persona_id"] for p in kept] == ["a"]
    assert audit["dropped_minor"] == 1
    assert audit["dropped_region_not_north_america"] == 1
    assert audit["dropped_region_missing"] == 1
    assert audit["dropped_age_missing"] == 1


def test_us_culture_filter_narrows_further(rake):
    personas = [
        {
            "persona_id": "a",
            "dimensions": {
                "region": "North America",
                "age_bracket": "35-44",
                "cult_united_states": "Native",
            },
        },
        {
            "persona_id": "b",
            "dimensions": {
                "region": "North America",
                "age_bracket": "35-44",
                "cult_united_states": "Unfamiliar",
            },
        },
    ]
    kept, audit = rake.select_us_adults(personas, require_us_culture=True)
    assert [p["persona_id"] for p in kept] == ["a"]
    assert audit["dropped_not_us_culture"] == 1


def test_off_schema_age_is_kept_but_uncoded(rake, schema_values):
    """The dev sample ships an off-schema '65+' value. Those personas are adults
    and should be kept, but they cannot be coded into an age target."""
    personas = [
        {"persona_id": "a", "dimensions": {"region": "North America", "age_bracket": "65+"}}
    ]
    kept, audit = rake.select_us_adults(personas, require_us_culture=False)
    assert len(kept) == 1
    assert audit["kept_with_off_schema_age"] == 1
    columns, uncodeable = rake.build_columns(kept, ["age_bracket"], schema_values)
    assert columns["age_bracket"][0] == -1
    assert uncodeable["age_bracket"] == 1


def test_omitting_a_populated_category_is_rejected(rake, schema_values):
    """The core silent-skew guard: omitting a category the pool actually contains
    deflates every supplied share, so it must be a hard error."""
    personas = [
        {"persona_id": "a", "dimensions": {"region": "North America", "age_bracket": "35-44"}},
        {"persona_id": "b", "dimensions": {"region": "North America", "age_bracket": "85+"}},
    ]
    columns, _ = rake.build_columns(personas, ["age_bracket"], schema_values)
    payload = {"dimensions": {"age_bracket": {"shares": {"35-44": 1.0}}}}
    with pytest.raises(SystemExit, match="85\\+"):
        rake.resolve_targets(payload, ["age_bracket"], schema_values, columns)


def test_omitting_an_absent_category_is_allowed(rake, schema_values):
    """By contrast, an omitted category with no rows contributes nothing to the
    denominator - this is what makes the adults-only age target correct."""
    personas = [
        {"persona_id": "a", "dimensions": {"region": "North America", "age_bracket": "35-44"}}
    ]
    columns, _ = rake.build_columns(personas, ["age_bracket"], schema_values)
    payload = {"dimensions": {"age_bracket": {"shares": {"35-44": 1.0}}}}
    resolved = rake.resolve_targets(payload, ["age_bracket"], schema_values, columns)
    assert resolved["age_bracket"]


def test_empty_shares_dimension_is_skipped_not_fatal(rake, schema_values):
    personas = [
        {
            "persona_id": "a",
            "dimensions": {
                "region": "North America",
                "age_bracket": "35-44",
                "socioeconomic_band": "Middle",
            },
        }
    ]
    columns, _ = rake.build_columns(
        personas, ["age_bracket", "socioeconomic_band"], schema_values
    )
    payload = {
        "dimensions": {
            "age_bracket": {"shares": {"35-44": 1.0}},
            "socioeconomic_band": {"shares": {}, "quality": "none"},
        }
    }
    resolved = rake.resolve_targets(
        payload, ["age_bracket", "socioeconomic_band"], schema_values, columns
    )
    assert set(resolved) == {"age_bracket"}


def test_structural_gaps_flags_unreachable_categories(rake):
    report = {
        "age_bracket": {
            "coded_rows": 40,
            "uncoded_rows": 0,
            "max_abs_residual": 0.07,
            "categories": [
                {"value": "35-44", "target": 0.9, "n": 40, "residual": 0.0},
                {"value": "85+", "target": 0.1, "n": 0, "residual": -0.1},
            ],
        }
    }
    gaps = rake.structural_gaps(report, min_coded=20)
    assert gaps["empty_target_categories"]["age_bracket"][0]["value"] == "85+"
    assert gaps["unreachable_population_share"]["age_bracket"] == pytest.approx(0.1)
    assert "age_bracket" not in gaps["undercoded_margins"]


def test_structural_gaps_flags_thin_margins(rake):
    report = {
        "urbanicity": {
            "coded_rows": 8,
            "uncoded_rows": 48,
            "max_abs_residual": 0.001,
            "categories": [{"value": "Rural", "target": 0.2, "n": 8, "residual": 0.0}],
        }
    }
    gaps = rake.structural_gaps(report, min_coded=20)
    assert gaps["undercoded_margins"]["urbanicity"] == 8


def test_effective_sample_size_penalises_uneven_weights(rake):
    even = rake.effective_sample_size(np.ones(10))
    assert even["effective_n"] == pytest.approx(10.0)
    assert even["design_effect"] == pytest.approx(1.0)

    skewed = rake.effective_sample_size(np.array([9.0] + [0.1] * 9))
    assert skewed["effective_n"] < 3.0
    assert skewed["design_effect"] > 3.0


@pytest.mark.skipif(not DEV_POOL.is_dir(), reason="dev persona sample not present")
def test_end_to_end_rake_on_dev_pool(rake, targets, schema_values):
    """The whole path must run, and must not silently claim to be representative."""
    personas = rake.load_pool(DEV_POOL)
    eligible, audit = rake.select_us_adults(personas, require_us_culture=False)
    assert eligible, "dev pool should contain North American adults"
    assert audit["kept"] == len(eligible)

    dimensions = targets["default_rake_dimensions"]
    columns, _ = rake.build_columns(eligible, dimensions, schema_values)
    resolved = rake.resolve_targets(targets, dimensions, schema_values, columns)
    columns = {name: columns[name] for name in resolved}
    weights = rake.rake_weights(columns, resolved)

    assert len(weights) == len(eligible)
    assert np.all(weights > 0)

    report = rake.margin_report(columns, resolved, weights, schema_values)
    for dimension, block in report.items():
        for row in block["categories"]:
            # Raking cannot move a category that has no rows; every other
            # category should land close to its target.
            if row["n"] > 0:
                assert abs(row["residual"]) < 0.05, f"{dimension}/{row['value']}"

    gaps = rake.structural_gaps(report)
    assert gaps["empty_target_categories"], (
        "the dev sample has known coverage holes - if this passes cleanly the "
        "pool changed and the README's numbers need revisiting"
    )
