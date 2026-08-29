#!/usr/bin/env python3
"""Aggregate a survey_us-economic-pressure cohort run into cohort-level marginals.

Reads every trial's `verifier/structured_output.json` under a job directory and
emits the constructs the task README benchmarks against: the perception gap, the
$400 cash-coverage rate, the deferral rate, per-question marginals, and the
persona-fidelity drift rate.

The cohort drawn by `rake_us_adults.py --mode sample` carries uniform weights
(design effect 1.0), so every rate here is an unweighted proportion over the
trials that answered the item. Nothing in this script turns a marginal into a
US population estimate: `region="North America"` pools the US with Canada and
Mexico, and the raking targets are status `draft_unverified`.

Usage:
    python application/scripts/aggregate_survey_cohort.py jobs/<job-dir> \
        [--json out.json]
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

# Top-box groupings that make a marginal comparable to the published figure the
# README cites. Each maps a question id to the option ids counted as a "yes".
TOP_BOX = {
    "q_own_finances": {"q_own_finances_doing_ok", "q_own_finances_thriving"},
    "q_national_economy": {
        "q_national_economy_good",
        "q_national_economy_excellent",
    },
    "q_cushion_months": {"q_cushion_months_3_6", "q_cushion_months_6_plus"},
    "q_vs_year_ago": {"q_vs_year_ago_worse", "q_vs_year_ago_much_worse"},
}
# q_retirement_track is asked of everyone but benchmarked among non-retirees.
RETIREMENT_NONRETIRED_EXCLUDE = {"q_retirement_track_retired"}


def wilson_ci(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval - well behaved at proportions near 0 and 1."""
    if n == 0:
        return (float("nan"), float("nan"))
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    halfwidth = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - halfwidth), min(1.0, centre + halfwidth))


def mean_ci(values: list[float], z: float = 1.96) -> tuple[float, float, float]:
    """Mean and normal-approximation CI half-width for a numeric construct."""
    n = len(values)
    if n == 0:
        return (float("nan"), float("nan"), float("nan"))
    mean = sum(values) / n
    if n < 2:
        return (mean, float("nan"), float("nan"))
    var = sum((v - mean) ** 2 for v in values) / (n - 1)
    half = z * math.sqrt(var / n)
    return (mean, mean - half, mean + half)


def load_trials(job_dir: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """Return (field-maps, ids of trials with no readable verifier output)."""
    trials: list[dict[str, Any]] = []
    missing: list[str] = []
    for trial_dir in sorted(p for p in job_dir.iterdir() if p.is_dir()):
        path = trial_dir / "verifier" / "structured_output.json"
        if not path.is_file():
            missing.append(trial_dir.name)
            continue
        try:
            payload = json.loads(path.read_text())
        except json.JSONDecodeError:
            missing.append(trial_dir.name)
            continue
        fields = {
            f["key"]: f.get("value")
            for f in payload.get("fields", [])
            if isinstance(f, dict) and "key" in f
        }
        fields["_trial"] = trial_dir.name
        trials.append(fields)
    return trials, missing


def rate(trials: list[dict[str, Any]], key: str) -> dict[str, Any]:
    """Proportion of trials where a boolean verifier field is true."""
    vals = [t[key] for t in trials if isinstance(t.get(key), bool)]
    n = len(vals)
    k = sum(vals)
    lo, hi = wilson_ci(k, n)
    return {"n": n, "k": k, "rate": (k / n) if n else float("nan"), "ci": [lo, hi]}


def top_box_rate(
    trials: list[dict[str, Any]],
    question: str,
    winners: set[str],
    exclude: set[str] | None = None,
) -> dict[str, Any]:
    key = f"question.{question}.response"
    vals = [t[key] for t in trials if isinstance(t.get(key), str)]
    if exclude:
        vals = [v for v in vals if v not in exclude]
    n = len(vals)
    k = sum(1 for v in vals if v in winners)
    lo, hi = wilson_ci(k, n)
    return {"n": n, "k": k, "rate": (k / n) if n else float("nan"), "ci": [lo, hi]}


def marginal(trials: list[dict[str, Any]], question: str) -> dict[str, Any]:
    key = f"question.{question}.response"
    counts: Counter[str] = Counter()
    n = 0
    for t in trials:
        v = t.get(key)
        if isinstance(v, str):
            counts[v] += 1
            n += 1
        elif isinstance(v, list):
            n += 1
            for item in v:
                if isinstance(item, str):
                    counts[item] += 1
    return {"n": n, "counts": dict(counts.most_common())}


def pct(x: float) -> str:
    return "n/a" if math.isnan(x) else f"{100 * x:5.1f}%"


def build_report(job_dir: Path) -> dict[str, Any]:
    trials, missing = load_trials(job_dir)

    gaps = [
        t["survey.derived.perception_gap"]
        for t in trials
        if isinstance(t.get("survey.derived.perception_gap"), (int, float))
    ]
    gap_mean, gap_lo, gap_hi = mean_ci(gaps)
    own = top_box_rate(trials, "q_own_finances", TOP_BOX["q_own_finances"])
    nat = top_box_rate(trials, "q_national_economy", TOP_BOX["q_national_economy"])

    fidelity_age = rate(trials, "survey.fidelity.fidelity_age_bracket")
    fidelity_emp = rate(trials, "survey.fidelity.fidelity_demo_employment_status")
    checks = fidelity_age["n"] + fidelity_emp["n"]
    matches = fidelity_age["k"] + fidelity_emp["k"]
    drift_lo, drift_hi = wilson_ci(checks - matches, checks)
    # A trial "drifted" if any fired check on it mismatched.
    per_trial = [
        all(
            t[k]
            for k in (
                "survey.fidelity.fidelity_age_bracket",
                "survey.fidelity.fidelity_demo_employment_status",
            )
            if isinstance(t.get(k), bool)
        )
        for t in trials
        if any(
            isinstance(
                t.get(k),
                bool,
            )
            for k in (
                "survey.fidelity.fidelity_age_bracket",
                "survey.fidelity.fidelity_demo_employment_status",
            )
        )
    ]
    trial_drift_lo, trial_drift_hi = wilson_ci(
        len(per_trial) - sum(per_trial), len(per_trial)
    )

    return {
        "job_dir": str(job_dir),
        "trials_found": len(trials) + len(missing),
        "trials_with_verifier_output": len(trials),
        "trials_missing_verifier_output": missing,
        "perception_gap": {
            "mean_0_1_scale": gap_mean,
            "ci_0_1_scale": [gap_lo, gap_hi],
            "n": len(gaps),
            "own_finances_scaled_mean": mean_ci(
                [
                    t["survey.derived.own_finances_scaled"]
                    for t in trials
                    if isinstance(
                        t.get("survey.derived.own_finances_scaled"), (int, float)
                    )
                ]
            )[0],
            "national_economy_scaled_mean": mean_ci(
                [
                    t["survey.derived.national_economy_scaled"]
                    for t in trials
                    if isinstance(
                        t.get("survey.derived.national_economy_scaled"), (int, float)
                    )
                ]
            )[0],
            "own_finances_top2_share": own,
            "national_economy_top2_share": nat,
            "top2_gap_points": 100 * (own["rate"] - nat["rate"]),
        },
        "covers_400_with_cash": rate(trials, "survey.derived.covers_400_with_cash"),
        "deferred_any_care": rate(trials, "survey.derived.deferred_any_care"),
        "cushion_3_months_plus": top_box_rate(
            trials, "q_cushion_months", TOP_BOX["q_cushion_months"]
        ),
        "worse_off_than_year_ago": top_box_rate(
            trials, "q_vs_year_ago", TOP_BOX["q_vs_year_ago"]
        ),
        "retirement_on_track_nonretired": top_box_rate(
            trials,
            "q_retirement_track",
            {"q_retirement_track_on_track"},
            exclude=RETIREMENT_NONRETIRED_EXCLUDE,
        ),
        "fidelity": {
            "age_bracket": fidelity_age,
            "employment_status": fidelity_emp,
            "checks_fired": checks,
            "checks_matched": matches,
            "drift_rate_per_check": (
                (checks - matches) / checks if checks else float("nan")
            ),
            "drift_ci_per_check": [drift_lo, drift_hi],
            "trials_with_any_check": len(per_trial),
            "trials_drifted": len(per_trial) - sum(per_trial),
            "drift_rate_per_trial": (
                (len(per_trial) - sum(per_trial)) / len(per_trial)
                if per_trial
                else float("nan")
            ),
            "drift_ci_per_trial": [trial_drift_lo, trial_drift_hi],
        },
        "marginals": {
            q: marginal(trials, q)
            for q in (
                "q_emergency_400",
                "q_cushion_months",
                "q_vs_year_ago",
                "q_own_finances",
                "q_national_economy",
                "q_cutbacks",
                "q_deferred",
                "q_borrowed",
                "q_price_driver",
                "q_personal_vs_national_prices",
                "q_retirement_track",
                "q_year_ahead",
            )
        },
    }


def print_report(r: dict[str, Any]) -> None:
    print(f"Job: {r['job_dir']}")
    print(
        f"Trials: {r['trials_with_verifier_output']} with verifier output "
        f"of {r['trials_found']} found"
    )
    if r["trials_missing_verifier_output"]:
        print(f"  missing output: {len(r['trials_missing_verifier_output'])}")

    g = r["perception_gap"]
    print("\n-- Perception gap (own finances vs national economy) --")
    print(
        f"  mean gap on 0-1 scale : {g['mean_0_1_scale']:.4f} "
        f"[{g['ci_0_1_scale'][0]:.4f}, {g['ci_0_1_scale'][1]:.4f}]  n={g['n']}"
    )
    print(f"  own finances (0-1)    : {g['own_finances_scaled_mean']:.4f}")
    print(f"  national economy (0-1): {g['national_economy_scaled_mean']:.4f}")
    print(
        f"  own finances top-2 box: {pct(g['own_finances_top2_share']['rate'])} "
        f"(n={g['own_finances_top2_share']['n']})   [README: ~72%]"
    )
    print(
        f"  economy good/excellent: {pct(g['national_economy_top2_share']['rate'])} "
        f"(n={g['national_economy_top2_share']['n']})   [README: ~22%]"
    )
    print(
        f"  top-2-box gap         : {g['top2_gap_points']:.1f} points"
        "   [README: ~50 points]"
    )

    for label, key, bench in (
        ("$400 covered with cash", "covers_400_with_cash", "~63%"),
        ("Deferred any health care", "deferred_any_care", "~28%"),
        ("3+ months of savings", "cushion_3_months_plus", "~54%"),
        ("Worse off than a year ago", "worse_off_than_year_ago", "~35%"),
        ("Retirement on track (non-retired)", "retirement_on_track_nonretired", "~34%"),
    ):
        d = r[key]
        print(
            f"\n-- {label} --\n  {pct(d['rate'])} "
            f"[{pct(d['ci'][0])}, {pct(d['ci'][1])}]  k={d['k']}/{d['n']}"
            f"   [README: {bench}]"
        )

    f = r["fidelity"]
    print("\n-- Persona fidelity (manipulation check, not a finding) --")
    print(
        f"  age self-report matches       : {pct(f['age_bracket']['rate'])} "
        f"(n={f['age_bracket']['n']})"
    )
    print(
        f"  employment self-report matches: {pct(f['employment_status']['rate'])} "
        f"(n={f['employment_status']['n']})"
    )
    print(
        f"  drift rate per check          : {pct(f['drift_rate_per_check'])} "
        f"[{pct(f['drift_ci_per_check'][0])}, {pct(f['drift_ci_per_check'][1])}]  "
        f"{f['checks_fired'] - f['checks_matched']}/{f['checks_fired']} checks"
    )
    print(
        f"  drift rate per trial          : {pct(f['drift_rate_per_trial'])} "
        f"[{pct(f['drift_ci_per_trial'][0])}, {pct(f['drift_ci_per_trial'][1])}]  "
        f"{f['trials_drifted']}/{f['trials_with_any_check']} trials"
    )
    print(
        "  (denominator < trial count: the check only fires when the persona "
        "record carries the dimension)"
    )

    print("\n-- Marginals --")
    for q, m in r["marginals"].items():
        print(f"  {q} (n={m['n']})")
        for opt, c in m["counts"].items():
            share = c / m["n"] if m["n"] else float("nan")
            print(f"      {opt:45s} {c:4d}  {pct(share)}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("job_dir", type=Path)
    ap.add_argument("--json", type=Path, help="also write the report as JSON")
    args = ap.parse_args()

    if not args.job_dir.is_dir():
        raise SystemExit(f"not a directory: {args.job_dir}")

    report = build_report(args.job_dir)
    print_report(report)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, indent=2))
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
