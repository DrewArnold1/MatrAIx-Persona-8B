#!/usr/bin/env python3
"""Reweight (or draw) a US-adult persona cohort against census marginals.

Two modes, because the right one depends on how big your pool is:

  --mode weight  Every eligible persona is kept and given a calibration weight.
                 Correct when the pool is small: with ~50 eligible personas you
                 cannot afford to throw any away, so you run all of them and
                 weight the answers.

  --mode sample  Draw a fixed-size cohort whose own margins match the targets,
                 via calibrate_inclusion_weights + deterministic_priority_sample.
                 Correct when the pool is large (Persona 1M) and the binding
                 constraint is how many trials you can pay for.

Two failure modes this script exists to prevent:

1. Minors in the pool. rake_weights counts every row with a non-negative code
   in the margin denominator, so a 12-year-old carries a valid age code and
   silently drags every adult share down. targets_us.json is an adults-only
   target set, so the pool MUST be filtered first. --min-adult-age enforces it.

2. Reading the output as a US estimate. The schema has no country dimension;
   region='North America' pools the US with Canada and Mexico. What comes out
   is 'North American adults reweighted to US marginals'. The report says so.

Usage:

    python persona/curation/existing_data/united_states/rake_us_adults.py \
      --pool persona/datasets/matraix-persona-dev-sample \
      --mode weight --out /tmp/us_adults_cohort.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Iterable, Iterator

import numpy as np
import yaml

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from persona.post_process.coreset_1m.calibration import (  # noqa: E402
    calibrate_inclusion_weights,
    deterministic_priority_sample,
    rake_weights,
)

DEFAULT_POOL = REPO_ROOT / "persona/datasets/matraix-persona-dev-sample"
DEFAULT_TARGETS = Path(__file__).resolve().parent / "targets_us.json"
DEFAULT_SCHEMA = REPO_ROOT / "persona/schema/dimensions.json"

# 'age_bracket' in the schema has no open-ended "65+" value, but the dev sample
# ships personas carrying it. Treat it as adult rather than dropping those rows,
# and report it, because it cannot be coded into any target category.
OFF_SCHEMA_ADULT_AGES = {"65+"}
ADULT_AGE_VALUES = (
    "18-24",
    "25-34",
    "35-44",
    "45-54",
    "55-64",
    "65-74",
    "75-84",
    "85+",
)
US_CULTURE_VALUES = {"Native", "Lived there"}


def load_schema_values(path: Path) -> dict[str, list[str]]:
    """Map dimension id -> ordered value list (the code order rake_weights uses)."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    dims = payload if isinstance(payload, list) else payload.get("dimensions", payload)
    if isinstance(dims, dict):
        items = dims.items()
    else:
        items = [(entry.get("id"), entry) for entry in dims]
    return {str(key): list(value.get("values") or []) for key, value in items}


def _import_1m_pool():
    """Import the production pool loader, which owns the Parquet codec/decoder.

    It lives under application/playground and pulls in packages/playground/src
    and src/, none of which are on sys.path when this script runs standalone.
    """
    for extra in ("application/playground", "packages/playground/src", "src"):
        path = str(REPO_ROOT / extra)
        if path not in sys.path:
            sys.path.insert(0, path)
    from backend.service import persona_1m_pool  # noqa: PLC0415

    return persona_1m_pool


def resolve_parquet_release(pool_dir: Path):
    """Return Persona1MPaths for a 1M-style release under pool_dir, else None.

    Probes with the pool loader's own layout check (_paths_from_root) so this
    agrees with resolve_1m_paths by construction rather than by a second,
    drifting copy of the glob. Accepts either the release root itself or a
    parent holding release/, which is where fetch_persona_1m.py lands it.
    """
    if not pool_dir.is_dir():
        return None
    try:
        pool = _import_1m_pool()
    except ImportError:
        return None
    for candidate in (pool_dir, pool_dir / "release"):
        paths = pool._paths_from_root(candidate)
        if paths is not None and paths.parquet_files:
            return paths
    return None


def load_pool_yaml(pool_dir: Path) -> list[dict[str, Any]]:
    """Load a directory of per-persona YAML files (the dev sample layout)."""
    personas = []
    for file in sorted(pool_dir.glob("*.yaml")):
        record = yaml.safe_load(file.read_text(encoding="utf-8"))
        if isinstance(record, dict) and record.get("persona_id"):
            personas.append(record)
    return personas


def load_pool_parquet(paths, *, progress_every: int = 100_000) -> Iterator[dict[str, Any]]:
    """Stream-decode a Persona 1M Parquet release into persona records.

    Delegates every byte of decoding to the pool module: the codec built from
    persona_codes.schema.json and _iter_decoded_rows, which already yields
    exactly the {persona_id, source, dimensions} shape the YAML path produces.
    Writing a second decoder here would be a second thing to keep in sync with
    the release format.

    This yields rather than returning a list on purpose. A decoded persona is a
    dict of ~1290 possible dimensions; materializing all 1M of them costs well
    over 10 GB and dies on an ordinary box, while only the ~3% that survive
    select_us_adults are ever needed. Consuming this lazily bounds peak memory
    by the eligible set, not the release.
    """
    pool = _import_1m_pool()
    codec = pool.load_codec(paths.schema_path)
    print(
        f"  decoding {len(paths.parquet_files)} parquet file(s) from {paths.data_dir} "
        "(streaming the whole release; this takes a few minutes)",
        file=sys.stderr,
        flush=True,
    )
    count = 0
    for count, row in enumerate(pool._iter_decoded_rows(paths, codec), start=1):
        if row.get("persona_id"):
            yield row
        if progress_every and count % progress_every == 0:
            print(f"    {count:,} rows decoded", file=sys.stderr, flush=True)
    print(f"    {count:,} rows decoded", file=sys.stderr, flush=True)


def load_pool(pool_dir: Path) -> Iterable[dict[str, Any]]:
    """Load a persona pool from either layout: YAML directory or 1M Parquet.

    The dev sample is one YAML file per persona; the published 1M release is
    Parquet with codes plus a schema. YAML is probed first so an explicit dev
    pool never pays for a release-layout probe.

    Returns a list for the (small) YAML layout and a lazy iterator for the 1M
    release. Callers must therefore iterate once and not index or re-scan; the
    only consumer, select_us_adults, does exactly that.
    """
    personas = load_pool_yaml(pool_dir)
    if personas:
        return personas

    paths = resolve_parquet_release(pool_dir)
    if paths is not None:
        return load_pool_parquet(paths)

    raise SystemExit(
        f"No persona pool found in {pool_dir}.\n"
        "Expected either *.yaml persona files (dev sample layout) or a Persona 1M\n"
        "release with data/persona-1m-*.parquet and persona_codes.schema.json.\n"
        "To import the 1M coreset:\n"
        "  python persona/scripts/fetch_persona_1m.py"
    )


ELIGIBLE_CACHE_FORMAT = 1


def read_eligible_cache(
    path: Path | None, *, pool: Path, require_us_culture: bool
) -> tuple[list[dict[str, Any]], dict[str, int]] | None:
    """Load a previously filtered eligible set, or None if it cannot be reused.

    Decoding the 1M release takes minutes, and comparing cohort sizes means
    doing it once per candidate size. The filtered set is the same every time,
    so it is cached after the first pass.

    A stale cache is worse than no cache: it would silently calibrate against
    the wrong pool. The header therefore records what the cache was built from,
    and any mismatch - format, pool or filter - is treated as a miss rather
    than an error, so a changed flag just costs a re-decode.
    """
    if path is None or not path.is_file():
        return None
    with path.open(encoding="utf-8") as handle:
        try:
            header = json.loads(handle.readline())
        except json.JSONDecodeError:
            return None
        if (
            header.get("cache_format") != ELIGIBLE_CACHE_FORMAT
            or header.get("pool") != str(pool)
            or header.get("us_culture_filter") is not require_us_culture
        ):
            return None
        personas = [json.loads(line) for line in handle if line.strip()]
    audit = header.get("audit")
    if not isinstance(audit, dict) or len(personas) != audit.get("kept"):
        return None
    return personas, audit


def write_eligible_cache(
    path: Path,
    personas: list[dict[str, Any]],
    audit: dict[str, int],
    *,
    pool: Path,
    require_us_culture: bool,
) -> None:
    """Write the eligible set as a header line plus one JSON object per row.

    Written to a temporary file and renamed, because a run interrupted midway
    through a 143k-row dump would otherwise leave a short file that reads as a
    complete, smaller pool - a silently wrong cohort rather than a crash.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    header = {
        "cache_format": ELIGIBLE_CACHE_FORMAT,
        "pool": str(pool),
        "us_culture_filter": require_us_culture,
        "audit": audit,
    }
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(header) + "\n")
        for persona in personas:
            handle.write(json.dumps(persona) + "\n")
    temporary.replace(path)


def select_us_adults(
    personas: Iterable[dict[str, Any]], *, require_us_culture: bool
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Filter to the US-adult proxy, returning survivors and a drop audit.

    Takes any iterable, not just a list, so the 1M Parquet release can be
    filtered as it streams: the survivors are held, the ~97% that are not
    US-adult proxies are counted and dropped without ever being accumulated.
    """
    adult_values = set(ADULT_AGE_VALUES) | OFF_SCHEMA_ADULT_AGES
    audit = {
        "pool_total": 0,
        "dropped_region_not_north_america": 0,
        "dropped_region_missing": 0,
        "dropped_age_missing": 0,
        "dropped_minor": 0,
        "dropped_not_us_culture": 0,
        "kept": 0,
        "kept_with_off_schema_age": 0,
    }
    kept = []
    for persona in personas:
        audit["pool_total"] += 1
        dims = persona.get("dimensions") or {}
        region = dims.get("region")
        if region is None:
            audit["dropped_region_missing"] += 1
            continue
        if region != "North America":
            audit["dropped_region_not_north_america"] += 1
            continue
        age = dims.get("age_bracket")
        if age is None:
            audit["dropped_age_missing"] += 1
            continue
        if age not in adult_values:
            audit["dropped_minor"] += 1
            continue
        if require_us_culture and dims.get("cult_united_states") not in US_CULTURE_VALUES:
            audit["dropped_not_us_culture"] += 1
            continue
        if age in OFF_SCHEMA_ADULT_AGES:
            audit["kept_with_off_schema_age"] += 1
        kept.append(persona)
    audit["kept"] = len(kept)
    return kept, audit


def build_columns(
    personas: list[dict[str, Any]],
    dimensions: list[str],
    schema_values: dict[str, list[str]],
) -> tuple[dict[str, np.ndarray], dict[str, int]]:
    """Encode personas as integer code columns; -1 means missing or uncodeable."""
    columns: dict[str, np.ndarray] = {}
    uncodeable: dict[str, int] = {}
    for dimension in dimensions:
        values = schema_values.get(dimension) or []
        codebook = {value: code for code, value in enumerate(values)}
        column = np.full(len(personas), -1, dtype=np.int64)
        misses = 0
        for row, persona in enumerate(personas):
            raw = (persona.get("dimensions") or {}).get(dimension)
            if raw is None:
                continue
            if raw in codebook:
                column[row] = codebook[raw]
            else:
                misses += 1
        columns[dimension] = column
        uncodeable[dimension] = misses
    return columns, uncodeable


def resolve_targets(
    payload: dict[str, Any],
    dimensions: list[str],
    schema_values: dict[str, list[str]],
    columns: dict[str, np.ndarray],
) -> dict[str, dict[int, float]]:
    """Convert label-keyed shares to the code-keyed form rake_weights expects.

    An omitted schema value is only safe when no eligible persona carries it -
    a category with zero rows contributes nothing to the margin denominator, so
    omitting it changes nothing. That is how the adults-only age target stays
    correct after the minor filter. But if the pool *does* contain rows in an
    omitted category, rake_weights counts them in the denominator while never
    giving them a target, which silently deflates every supplied share. So the
    check is empirical, against the filtered pool, not against the declaration.
    """
    resolved: dict[str, dict[int, float]] = {}
    for dimension in dimensions:
        definition = payload["dimensions"].get(dimension)
        if definition is None:
            raise SystemExit(f"targets file has no dimension '{dimension}'")
        shares = definition.get("shares") or {}
        if not shares:
            print(
                f"  skipping '{dimension}': shares are deliberately empty "
                f"({definition.get('quality')})",
                file=sys.stderr,
            )
            continue
        values = schema_values.get(dimension) or []
        codebook = {value: code for code, value in enumerate(values)}
        unknown = sorted(set(shares) - set(codebook))
        if unknown:
            raise SystemExit(
                f"targets['{dimension}'] uses labels absent from the schema: {unknown}"
            )
        column = columns[dimension]
        populated = {int(code) for code in np.unique(column) if code >= 0}
        omitted_but_present = sorted(
            values[code] for code in populated if values[code] not in shares
        )
        if omitted_but_present:
            raise SystemExit(
                f"targets['{dimension}'] omits {omitted_but_present}, but the filtered "
                "pool contains personas in those categories. rake_weights counts them "
                "in the margin denominator without ever targeting them, which deflates "
                "every supplied share. Either give them an explicit share or filter "
                "them out of the pool."
            )
        resolved[dimension] = {
            codebook[label]: float(share) for label, share in shares.items()
        }
    if not resolved:
        raise SystemExit("no calibratable dimensions remain")
    return resolved


def margin_report(
    columns: dict[str, np.ndarray],
    targets: dict[str, dict[int, float]],
    weights: np.ndarray,
    schema_values: dict[str, list[str]],
) -> dict[str, Any]:
    """Compare unweighted and weighted margins against the targets."""
    report: dict[str, Any] = {}
    for dimension, target in targets.items():
        column = columns[dimension]
        known = column >= 0
        labels = schema_values[dimension]
        raw_known = int(known.sum())
        weighted_known = float(weights[known].sum())
        rows = []
        for code, share in sorted(target.items()):
            member = column == code
            unweighted = float(member.sum() / raw_known) if raw_known else None
            weighted = (
                float(weights[member].sum() / weighted_known) if weighted_known else None
            )
            rows.append(
                {
                    "value": labels[code],
                    "target": round(share, 5),
                    "unweighted": None if unweighted is None else round(unweighted, 5),
                    "weighted": None if weighted is None else round(weighted, 5),
                    "n": int(member.sum()),
                    "residual": (
                        None if weighted is None else round(weighted - share, 5)
                    ),
                }
            )
        report[dimension] = {
            "coded_rows": raw_known,
            "uncoded_rows": int((~known).sum()),
            "max_abs_residual": max(
                (abs(row["residual"]) for row in rows if row["residual"] is not None),
                default=None,
            ),
            "categories": rows,
        }
    return report


def structural_gaps(report: dict[str, Any], min_coded: int = 20) -> dict[str, Any]:
    """Find the failures reweighting cannot fix.

    Raking rescales the rows you have. It cannot manufacture a category the pool
    never sampled: a target category with zero eligible personas stays at zero
    however the weights move. Those are coverage holes, not fit errors, and they
    do not show up in a residual - a 0.0706 residual on an empty cell looks like
    a near miss when it is actually an unreachable category. Separately, a margin
    fit on a handful of coded rows will report a tiny residual because a few
    weights absorbed it, which is overfitting, not representativeness.
    """
    empty: dict[str, list[dict[str, Any]]] = {}
    sparse: dict[str, int] = {}
    for dimension, block in report.items():
        holes = [
            {"value": row["value"], "target": row["target"]}
            for row in block["categories"]
            if row["n"] == 0 and row["target"] > 0
        ]
        if holes:
            empty[dimension] = holes
        if block["coded_rows"] < min_coded:
            sparse[dimension] = block["coded_rows"]
    unreachable = {
        dimension: round(sum(hole["target"] for hole in holes), 4)
        for dimension, holes in empty.items()
    }
    return {
        "empty_target_categories": empty,
        "unreachable_population_share": unreachable,
        "undercoded_margins": sparse,
        "undercoded_threshold": min_coded,
    }


def effective_sample_size(weights: np.ndarray) -> dict[str, float]:
    """Kish's effective n - what the weighting actually costs you in precision."""
    total = float(weights.sum())
    if total <= 0:
        return {"n": 0.0, "effective_n": 0.0, "design_effect": 0.0, "efficiency": 0.0}
    effective = total**2 / float((weights**2).sum())
    return {
        "n": float(len(weights)),
        "effective_n": round(effective, 2),
        "design_effect": round(len(weights) / effective, 3) if effective else 0.0,
        "efficiency": round(effective / len(weights), 3),
        "weight_min": round(float(weights.min()), 4),
        "weight_max": round(float(weights.max()), 4),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reweight or draw a US-adult persona cohort against census marginals."
    )
    parser.add_argument("--pool", type=Path, default=DEFAULT_POOL)
    parser.add_argument("--targets", type=Path, default=DEFAULT_TARGETS)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument(
        "--dims",
        default=None,
        help="Comma-separated dimensions to calibrate on "
        "(default: targets file default_rake_dimensions).",
    )
    parser.add_argument("--mode", choices=["weight", "sample"], default="weight")
    parser.add_argument(
        "--sample-size", type=int, default=None, help="Cohort size for --mode sample."
    )
    parser.add_argument(
        "--us-culture",
        action="store_true",
        help="Narrow to cult_united_states in {Native, Lived there}. Sharper US proxy, "
        "far smaller pool, and biased toward personas whose culture field is populated.",
    )
    parser.add_argument(
        "--eligible-cache",
        type=Path,
        default=None,
        help="Cache the filtered eligible set here and reuse it on later runs, "
        "skipping the multi-minute Parquet decode. Rebuilt automatically when "
        "--pool or --us-culture changes.",
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    schema_values = load_schema_values(args.schema)
    targets_payload = json.loads(args.targets.read_text(encoding="utf-8"))
    dimensions = (
        [d.strip() for d in args.dims.split(",") if d.strip()]
        if args.dims
        else list(targets_payload.get("default_rake_dimensions") or [])
    )

    cached = read_eligible_cache(
        args.eligible_cache, pool=args.pool, require_us_culture=args.us_culture
    )
    if cached is not None:
        eligible, audit = cached
        print(f"Pool: {args.pool} (eligible set from {args.eligible_cache})")
    else:
        personas = load_pool(args.pool)
        eligible, audit = select_us_adults(personas, require_us_culture=args.us_culture)
        if args.eligible_cache is not None:
            write_eligible_cache(
                args.eligible_cache,
                eligible,
                audit,
                pool=args.pool,
                require_us_culture=args.us_culture,
            )
            print(f"  cached eligible set to {args.eligible_cache}")
        print(f"Pool: {args.pool}")
    print(f"  {audit['pool_total']} personas -> {audit['kept']} eligible US-adult proxy")
    for key, count in audit.items():
        if key.startswith("dropped_") and count:
            print(f"    {key}: {count}")
    if audit["kept_with_off_schema_age"]:
        print(
            f"    note: {audit['kept_with_off_schema_age']} kept rows carry the "
            "off-schema age value '65+', which cannot be coded into an age target "
            "and is treated as missing for that margin."
        )
    if not eligible:
        raise SystemExit("no eligible personas - nothing to calibrate")

    print(f"Calibrating on: {', '.join(dimensions)}")
    columns, uncodeable = build_columns(eligible, dimensions, schema_values)
    for dimension, misses in uncodeable.items():
        if misses:
            print(f"    {dimension}: {misses} rows carry off-schema values (uncoded)")
    targets = resolve_targets(targets_payload, dimensions, schema_values, columns)
    columns = {name: columns[name] for name in targets}

    if args.mode == "sample":
        size = args.sample_size or min(len(eligible), 100)
        if size > len(eligible):
            raise SystemExit(
                f"--sample-size {size} exceeds the {len(eligible)} eligible personas. "
                "Import Persona 1M for a pool large enough to sample from."
            )
        race_weights = calibrate_inclusion_weights(columns, targets, size)
        selected = deterministic_priority_sample(
            [p["persona_id"] for p in eligible], race_weights, size, args.seed
        )
        chosen = sorted(int(index) for index in selected)
        eligible = [eligible[index] for index in chosen]
        columns = {name: column[chosen] for name, column in columns.items()}
        weights = np.ones(len(eligible), dtype=np.float64)
    else:
        weights = rake_weights(columns, targets)

    report = margin_report(columns, targets, weights, schema_values)
    diagnostics = effective_sample_size(weights)
    gaps = structural_gaps(report)

    print()
    for dimension, block in report.items():
        worst = block["max_abs_residual"]
        print(f"  {dimension}: max |residual| = {worst}")
        for row in block["categories"]:
            # weighted is None when a margin has no weighted mass at all; format
            # it as text rather than letting str.format choke on NoneType.
            weighted = "n/a" if row["weighted"] is None else f"{row['weighted']:.5f}"
            print(
                f"      {row['value']:<20} target {row['target']:<8} "
                f"weighted {weighted:<8} n={row['n']}"
            )
    print()
    print(
        f"  n={diagnostics['n']:.0f}  effective_n={diagnostics['effective_n']}  "
        f"design_effect={diagnostics['design_effect']}"
    )
    if diagnostics["efficiency"] < 0.5:
        print(
            f"  WARNING: weighting discards {(1 - diagnostics['efficiency']) * 100:.0f}% "
            "of the statistical power. The pool is far enough from US adults that a "
            "few personas carry most of the weight."
        )
    for dimension, holes in gaps["empty_target_categories"].items():
        share = gaps["unreachable_population_share"][dimension]
        names = ", ".join(hole["value"] for hole in holes)
        print(
            f"  COVERAGE HOLE {dimension}: no personas in [{names}] "
            f"= {share * 100:.1f}% of US adults, unreachable by any weighting."
        )
    for dimension, coded in gaps["undercoded_margins"].items():
        print(
            f"  THIN MARGIN {dimension}: only {coded} personas carry this dimension, "
            "so its close fit is overfitting, not representativeness."
        )

    payload = {
        "schemaVersion": "1.0",
        "artifactType": "matraix.us_adult_cohort",
        "mode": args.mode,
        "seed": args.seed,
        "pool": str(args.pool),
        "targets": str(args.targets),
        "targets_scope": targets_payload.get("scope"),
        "targets_status": targets_payload.get("status"),
        "calibrated_dimensions": list(targets),
        "us_culture_filter": args.us_culture,
        "selection_audit": audit,
        "diagnostics": diagnostics,
        "structural_gaps": gaps,
        "margins": report,
        "interpretation": (
            "North American adults reweighted to US marginals - NOT a US probability "
            "sample. The schema has no country dimension, and the target shares in "
            f"{args.targets.name} are status={targets_payload.get('status')!r}."
        ),
        "personas": [
            {
                "persona_id": persona["persona_id"],
                "source": persona.get("source"),
                "weight": round(float(weight), 6),
            }
            for persona, weight in zip(eligible, weights)
        ],
    }
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"  wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
