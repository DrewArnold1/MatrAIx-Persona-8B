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


# --- Parquet pool layout -------------------------------------------------
#
# The dev sample is a directory of per-persona YAML; the published Persona 1M
# release is Parquet plus a codes schema. load_pool has to read both, and the
# Parquet path has to go through the pool module's own codec rather than a
# second decoder that can drift from the release format. These tests build a
# tiny synthetic release in the real on-disk shape so they run without the
# multi-GB download.

RELEASE_DIMENSIONS = {
    "region": ["North America", "South America", "Europe", "Africa"],
    "age_bracket": [
        "Under 5",
        "5-12",
        "13-17",
        "18-24",
        "25-34",
        "35-44",
        "45-54",
        "55-64",
        "65-74",
        "75-84",
        "85+",
    ],
    "gender_identity": ["Man", "Woman", "Non-binary"],
    "cult_united_states": ["Native", "Lived there", "Familiar", "Unfamiliar"],
}


def _write_synthetic_release(root: Path, records: list[dict[str, str]]) -> Path:
    """Write a minimal Persona 1M-shaped release: data/*.parquet + codes schema.

    Mirrors the published layout exactly - the same file glob, the same column
    names, the same packed-nibble attribute encoding - because the point of the
    test is that the loader's real layout probe and real codec accept it.
    """
    pytest.importorskip("pyarrow")
    import pyarrow as pa
    import pyarrow.parquet as pq

    from persona.post_process.unified_dataset.schema import ATTRIBUTE_COUNT, AttributeCodec

    # The codec is positional over a fixed-width column list, so the schema has
    # to carry exactly ATTRIBUTE_COUNT columns. Ours go first; the rest is inert
    # filler standing in for the dimensions this test does not exercise.
    columns = [
        {"id": name, "values": values} for name, values in RELEASE_DIMENSIONS.items()
    ]
    columns += [
        {"id": f"filler_{index}", "values": ["a", "b"]}
        for index in range(ATTRIBUTE_COUNT - len(columns))
    ]
    data_dir = root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    schema_path = root / "persona_codes.schema.json"
    schema_path.write_text(json.dumps({"columns": columns}), encoding="utf-8")

    codec = AttributeCodec.from_codes_schema(schema_path)
    attributes, bitmaps, overrides = [], [], []
    for record in records:
        packed, nulls, over = codec.encode_mapping(record)
        attributes.append(packed)
        bitmaps.append(nulls)
        overrides.append(over)

    table = pa.table(
        {
            "source": pa.array([f"synthetic-{i}" for i in range(len(records))]),
            "source_row_index": pa.array(list(range(len(records))), type=pa.int64()),
            "attributes": pa.array(attributes, type=pa.binary()),
            "null_bitmap": pa.array(bitmaps, type=pa.binary()),
            "attribute_overrides": pa.array(
                overrides,
                type=pa.list_(
                    pa.struct([("field_index", pa.int32()), ("value", pa.string())])
                ),
            ),
        }
    )
    pq.write_table(table, data_dir / "persona-1m-0000.parquet")
    return root


@pytest.fixture
def synthetic_release(tmp_path):
    """Four personas: two US-adult proxies, one minor, one outside the region."""
    records = [
        {"region": "North America", "age_bracket": "35-44", "gender_identity": "Woman"},
        {"region": "North America", "age_bracket": "65-74", "gender_identity": "Man"},
        {"region": "North America", "age_bracket": "13-17", "gender_identity": "Man"},
        {"region": "Europe", "age_bracket": "25-34", "gender_identity": "Woman"},
    ]
    return _write_synthetic_release(tmp_path / "pool", records)


def test_load_pool_reads_a_parquet_release(rake, synthetic_release):
    """Parquet rows must arrive in the same shape the YAML path produces."""
    personas = list(rake.load_pool(synthetic_release))
    assert len(personas) == 4
    for persona in personas:
        assert persona["persona_id"], "every row needs an id to be sampled by"
        assert persona["source"]
        assert isinstance(persona["dimensions"], dict)
    assert {p["dimensions"]["age_bracket"] for p in personas} == {
        "35-44",
        "65-74",
        "13-17",
        "25-34",
    }


def test_load_pool_finds_a_release_subdirectory(rake, tmp_path):
    """fetch_persona_1m.py lands the release under <pool>/release/, not <pool>/."""
    pool = tmp_path / "matraix-persona-1m"
    _write_synthetic_release(
        pool / "release",
        [{"region": "North America", "age_bracket": "45-54"}],
    )
    assert len(list(rake.load_pool(pool))) == 1


def test_parquet_rows_flow_through_the_us_adult_filter(rake, synthetic_release):
    """The minor and the non-North-American must be dropped, as in the YAML path.

    This is the trap the script exists to close: a 12-year-old carries a valid
    age code, so a minor left in the pool would silently inflate every margin
    denominator. Decoding from Parquet must not reopen it.
    """
    personas = rake.load_pool(synthetic_release)
    eligible, audit = rake.select_us_adults(personas, require_us_culture=False)
    assert audit["pool_total"] == 4
    assert audit["dropped_minor"] == 1
    assert audit["dropped_region_not_north_america"] == 1
    assert audit["kept"] == 2
    assert {p["dimensions"]["age_bracket"] for p in eligible} == {"35-44", "65-74"}


def test_yaml_layout_wins_when_both_are_present(rake, tmp_path):
    """A YAML pool must never be read through the Parquet decoder by accident."""
    pool = tmp_path / "mixed"
    _write_synthetic_release(pool, [{"region": "North America", "age_bracket": "25-34"}])
    (pool / "solo.yaml").write_text(
        json.dumps(
            {
                "persona_id": "yaml-only",
                "dimensions": {"region": "North America", "age_bracket": "55-64"},
            }
        ),
        encoding="utf-8",
    )
    personas = rake.load_pool(pool)
    assert [p["persona_id"] for p in personas] == ["yaml-only"]


def test_resolve_parquet_release_rejects_an_unrelated_directory(rake, tmp_path):
    """A stray directory of parquet files is not a release - the schema is required."""
    assert rake.resolve_parquet_release(tmp_path / "missing") is None

    bare = tmp_path / "bare"
    (bare / "data").mkdir(parents=True)
    (bare / "data" / "persona-1m-0000.parquet").write_bytes(b"not really parquet")
    assert rake.resolve_parquet_release(bare) is None, (
        "parquet files without persona_codes.schema.json cannot be decoded"
    )


def test_empty_pool_names_both_supported_layouts(rake, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(SystemExit) as excinfo:
        rake.load_pool(empty)
    message = str(excinfo.value)
    assert "yaml" in message.lower()
    assert "parquet" in message.lower()
    assert "fetch_persona_1m.py" in message


def test_parquet_pool_is_streamed_not_materialized(rake, synthetic_release):
    """A decoded 1M pool does not fit in memory, so the Parquet path must be lazy.

    Materializing all 1M decoded personas costs well over 10 GB and dies before
    it reaches the filter, even though only ~3% of rows survive it. Guard the
    laziness directly: nothing may be decoded until the caller iterates.
    """
    personas = rake.load_pool(synthetic_release)
    assert not isinstance(personas, list), (
        "load_pool must return a lazy iterator for the Parquet layout"
    )
    assert next(iter(personas))["persona_id"]


def test_select_us_adults_consumes_a_one_shot_iterator(rake):
    """The filter must count the pool as it streams, not call len() on it.

    pool_total came from len(personas) when every pool was a list. Against a
    generator that is a TypeError, and quietly reporting 0 would be worse.
    """
    records = [
        {"persona_id": "a", "dimensions": {"region": "North America", "age_bracket": "35-44"}},
        {"persona_id": "b", "dimensions": {"region": "North America", "age_bracket": "13-17"}},
        {"persona_id": "c", "dimensions": {"region": "Europe", "age_bracket": "45-54"}},
    ]
    eligible, audit = rake.select_us_adults(iter(records), require_us_culture=False)
    assert audit["pool_total"] == 3
    assert audit["dropped_minor"] == 1
    assert audit["dropped_region_not_north_america"] == 1
    assert [p["persona_id"] for p in eligible] == ["a"]


# --- eligible-set cache ---------------------------------------------------
#
# Decoding the 1M release takes minutes, so the filtered eligible set is
# cached. The risk a cache introduces is calibrating against the wrong pool
# without noticing, so these pin the invalidation rules rather than the speed.

CACHE_PERSONAS = [
    {
        "persona_id": "a",
        "source": "synthetic",
        "dimensions": {"region": "North America", "age_bracket": "35-44"},
    },
    {
        "persona_id": "b",
        "source": "synthetic",
        "dimensions": {"region": "North America", "age_bracket": "65-74"},
    },
]
CACHE_AUDIT = {"pool_total": 9, "kept": 2, "dropped_minor": 3}


def test_eligible_cache_round_trips(rake, tmp_path):
    cache = tmp_path / "eligible.jsonl"
    rake.write_eligible_cache(
        cache, CACHE_PERSONAS, CACHE_AUDIT, pool=Path("pool-a"), require_us_culture=False
    )
    loaded = rake.read_eligible_cache(
        cache, pool=Path("pool-a"), require_us_culture=False
    )
    assert loaded is not None
    personas, audit = loaded
    assert personas == CACHE_PERSONAS
    assert audit == CACHE_AUDIT, "the drop audit must survive, not be recomputed as 0"


def test_eligible_cache_misses_on_a_different_pool(rake, tmp_path):
    """Reusing pool A's eligible set for pool B would calibrate the wrong data."""
    cache = tmp_path / "eligible.jsonl"
    rake.write_eligible_cache(
        cache, CACHE_PERSONAS, CACHE_AUDIT, pool=Path("pool-a"), require_us_culture=False
    )
    assert rake.read_eligible_cache(
        cache, pool=Path("pool-b"), require_us_culture=False
    ) is None


def test_eligible_cache_misses_when_the_culture_filter_changes(rake, tmp_path):
    """--us-culture changes which personas are eligible, so it changes the cache."""
    cache = tmp_path / "eligible.jsonl"
    rake.write_eligible_cache(
        cache, CACHE_PERSONAS, CACHE_AUDIT, pool=Path("pool-a"), require_us_culture=False
    )
    assert rake.read_eligible_cache(
        cache, pool=Path("pool-a"), require_us_culture=True
    ) is None


def test_eligible_cache_misses_on_a_truncated_file(rake, tmp_path):
    """A run killed mid-dump must not read back as a complete, smaller pool.

    This is the failure the atomic rename exists to prevent; the row-count
    check is the backstop if a short file appears some other way.
    """
    cache = tmp_path / "eligible.jsonl"
    rake.write_eligible_cache(
        cache, CACHE_PERSONAS, CACHE_AUDIT, pool=Path("pool-a"), require_us_culture=False
    )
    lines = cache.read_text(encoding="utf-8").splitlines()
    cache.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
    assert rake.read_eligible_cache(
        cache, pool=Path("pool-a"), require_us_culture=False
    ) is None


def test_eligible_cache_write_is_atomic(rake, tmp_path):
    """The cache appears whole or not at all, and leaves no .tmp behind."""
    cache = tmp_path / "eligible.jsonl"
    rake.write_eligible_cache(
        cache, CACHE_PERSONAS, CACHE_AUDIT, pool=Path("pool-a"), require_us_culture=False
    )
    assert cache.is_file()
    assert not list(tmp_path.glob("*.tmp"))


def test_missing_eligible_cache_is_a_miss_not_an_error(rake, tmp_path):
    assert rake.read_eligible_cache(
        None, pool=Path("pool-a"), require_us_culture=False
    ) is None
    assert rake.read_eligible_cache(
        tmp_path / "absent.jsonl", pool=Path("pool-a"), require_us_culture=False
    ) is None


def test_cached_eligible_set_matches_an_uncached_filter(rake, synthetic_release, tmp_path):
    """The cache must be a shortcut to the same answer, not a different one."""
    direct, direct_audit = rake.select_us_adults(
        rake.load_pool(synthetic_release), require_us_culture=False
    )
    cache = tmp_path / "eligible.jsonl"
    rake.write_eligible_cache(
        cache, direct, direct_audit, pool=synthetic_release, require_us_culture=False
    )
    cached, cached_audit = rake.read_eligible_cache(
        cache, pool=synthetic_release, require_us_culture=False
    )
    assert cached == direct
    assert cached_audit == direct_audit
