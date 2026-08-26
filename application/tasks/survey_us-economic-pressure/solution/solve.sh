#!/bin/bash
set -euo pipefail

mkdir -p /app/output

python3 <<'PY'
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - container usually has PyYAML
    yaml = None

OUTPUT = Path("/app/output/survey_result.json")
PERSONA_PATH = Path("/app/input/persona.yaml")
QUESTIONNAIRE_CANDIDATES = (
    Path("/app/input/questionnaire.yaml"),
    Path("/app/input/input/questionnaire.yaml"),
)

# Oracle paths keyed on socioeconomic_band. These are a deterministic fixture
# for plumbing tests, NOT a prediction of how US adults answer - they are a
# monotone gradient by design so the verifier and reporting path can be
# exercised without a model. Never read an oracle run as a finding.
PATHS: dict[str, dict[str, object]] = {
    "Low income": {
        "q_emergency_400": "q_emergency_400_could_not",
        "q_cushion_months": "q_cushion_months_none",
        "q_vs_year_ago": "q_vs_year_ago_much_worse",
        "q_own_finances": "q_own_finances_struggling",
        "q_national_economy": "q_national_economy_poor",
        "q_cutbacks": [
            "q_cutbacks_store_brand",
            "q_cutbacks_eat_out_less",
            "q_cutbacks_energy",
            "q_cutbacks_extra_work",
        ],
        "q_deferred": [
            "q_deferred_medical",
            "q_deferred_dental",
            "q_deferred_prescription",
            "q_deferred_bill",
        ],
        "q_borrowed": "q_borrowed_high_cost",
        "q_price_driver": "q_price_driver_corporate",
        "q_personal_vs_national_prices": "q_personal_vs_national_prices_much_more",
        "q_retirement_track": "q_retirement_track_none",
        "q_year_ahead": "q_year_ahead_worse",
        "q_financial_stress": 5,
        "q_tradeoff_text": (
            "I put off a dental filling in the spring because the quote was more "
            "than my share of that month's rent. I switched to store-brand "
            "groceries the same month and it still did not close the gap."
        ),
        "q_self_age": "q_self_age_35_44",
        "q_self_employment": "q_self_employment_part_time",
    },
    "Lower-middle": {
        "q_emergency_400": "q_emergency_400_card_carry",
        "q_cushion_months": "q_cushion_months_under_1",
        "q_vs_year_ago": "q_vs_year_ago_worse",
        "q_own_finances": "q_own_finances_just_getting_by",
        "q_national_economy": "q_national_economy_poor",
        "q_cutbacks": [
            "q_cutbacks_store_brand",
            "q_cutbacks_eat_out_less",
            "q_cutbacks_cancel_subscription",
            "q_cutbacks_delay_purchase",
        ],
        "q_deferred": ["q_deferred_dental", "q_deferred_car_or_home"],
        "q_borrowed": "q_borrowed_credit",
        "q_price_driver": "q_price_driver_corporate",
        "q_personal_vs_national_prices": "q_personal_vs_national_prices_more",
        "q_retirement_track": "q_retirement_track_behind",
        "q_year_ahead": "q_year_ahead_same",
        "q_financial_stress": 4,
        "q_tradeoff_text": (
            "We cancelled two streaming services and pushed the car's timing belt "
            "service to next year. The car is the one that worries me."
        ),
        "q_self_age": "q_self_age_45_54",
        "q_self_employment": "q_self_employment_full_time",
    },
    "Middle": {
        "q_emergency_400": "q_emergency_400_card_full",
        "q_cushion_months": "q_cushion_months_1_3",
        "q_vs_year_ago": "q_vs_year_ago_same",
        "q_own_finances": "q_own_finances_doing_ok",
        "q_national_economy": "q_national_economy_fair",
        "q_cutbacks": ["q_cutbacks_eat_out_less", "q_cutbacks_delay_purchase"],
        "q_deferred": ["q_deferred_none"],
        "q_borrowed": "q_borrowed_no",
        "q_price_driver": "q_price_driver_supply",
        "q_personal_vs_national_prices": "q_personal_vs_national_prices_same",
        "q_retirement_track": "q_retirement_track_behind",
        "q_year_ahead": "q_year_ahead_same",
        "q_financial_stress": 3,
        "q_tradeoff_text": (
            "We kept the same car instead of replacing it this year and are "
            "eating out about twice a month instead of weekly."
        ),
        "q_self_age": "q_self_age_35_44",
        "q_self_employment": "q_self_employment_full_time",
    },
    "Upper-middle": {
        "q_emergency_400": "q_emergency_400_cash",
        "q_cushion_months": "q_cushion_months_3_6",
        "q_vs_year_ago": "q_vs_year_ago_better",
        "q_own_finances": "q_own_finances_doing_ok",
        "q_national_economy": "q_national_economy_fair",
        "q_cutbacks": ["q_cutbacks_eat_out_less"],
        "q_deferred": ["q_deferred_none"],
        "q_borrowed": "q_borrowed_no",
        "q_price_driver": "q_price_driver_government",
        "q_personal_vs_national_prices": "q_personal_vs_national_prices_same",
        "q_retirement_track": "q_retirement_track_on_track",
        "q_year_ahead": "q_year_ahead_better",
        "q_financial_stress": 2,
        "q_tradeoff_text": (
            "Groceries are noticeably higher so we cook at home more, but nothing "
            "we needed got postponed this year."
        ),
        "q_self_age": "q_self_age_45_54",
        "q_self_employment": "q_self_employment_full_time",
    },
    "High income": {
        "q_emergency_400": "q_emergency_400_cash",
        "q_cushion_months": "q_cushion_months_6_plus",
        "q_vs_year_ago": "q_vs_year_ago_much_better",
        "q_own_finances": "q_own_finances_thriving",
        "q_national_economy": "q_national_economy_good",
        "q_cutbacks": ["q_cutbacks_none"],
        "q_deferred": ["q_deferred_none"],
        "q_borrowed": "q_borrowed_no",
        "q_price_driver": "q_price_driver_government",
        "q_personal_vs_national_prices": "q_personal_vs_national_prices_less",
        "q_retirement_track": "q_retirement_track_on_track",
        "q_year_ahead": "q_year_ahead_better",
        "q_financial_stress": 1,
        "q_tradeoff_text": (
            "Prices are higher but they have not changed what we buy. We delayed "
            "a kitchen renovation for scheduling reasons, not cost."
        ),
        "q_self_age": "q_self_age_55_64",
        "q_self_employment": "q_self_employment_self",
    },
}
DEFAULT_BAND = "Middle"


def _band() -> str:
    if not PERSONA_PATH.is_file():
        return DEFAULT_BAND
    text = PERSONA_PATH.read_text(encoding="utf-8")
    for line in text.splitlines():
        if "socioeconomic_band:" in line:
            value = line.split(":", 1)[1].strip().strip("'\"")
            if value in PATHS:
                return value
    return DEFAULT_BAND


def _load_instrument() -> dict:
    for path in QUESTIONNAIRE_CANDIDATES:
        if path.is_file() and yaml is not None:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if isinstance(data, dict) and data.get("questions"):
                return data
    return {
        "id": "us_economic_pressure_v1",
        "title": "Household Financial Pressure",
        "questions": [
            {"id": key, "prompt": key, "type": "single_choice"}
            for key in PATHS[DEFAULT_BAND]
        ],
    }


def _ts(base: datetime, offset: int) -> str:
    return (base + timedelta(seconds=offset)).isoformat().replace("+00:00", "Z")


instrument = _load_instrument()
choices = PATHS.get(_band(), PATHS[DEFAULT_BAND])
questions = list(instrument.get("questions") or [])
answers = []
for question in questions:
    qid = str(question.get("id") or "").strip()
    if not qid or qid not in choices:
        continue
    answers.append(
        {
            "questionId": qid,
            "prompt": str(question.get("prompt") or qid),
            "value": choices[qid],
        }
    )

base = datetime.now(timezone.utc).replace(microsecond=0)
instrument_id = str(instrument.get("id") or "us_economic_pressure_v1")
trajectory = [
    {
        "timestamp": _ts(base, 0),
        "actor": "system",
        "action": "survey_started",
        "context": {
            "instrumentId": instrument_id,
            "instrumentTitle": str(instrument.get("title") or ""),
            "numQuestions": len(questions),
        },
        "outcome": {"status": "started"},
    }
]
offset = 1
for index, question in enumerate(questions, start=1):
    qid = str(question.get("id") or "").strip()
    if not qid:
        continue
    qctx = {
        "instrumentId": instrument_id,
        "questionId": qid,
        "questionIndex": index,
        "questionType": str(question.get("type") or ""),
        "construct": str(question.get("construct") or ""),
    }
    trajectory.append(
        {
            "timestamp": _ts(base, offset),
            "actor": "assistant",
            "action": "ask_question",
            "context": qctx,
            "outcome": {"prompt": str(question.get("prompt") or qid)},
        }
    )
    offset += 1
    if qid in choices:
        trajectory.append(
            {
                "timestamp": _ts(base, offset),
                "actor": "user",
                "action": "answer_question",
                "context": qctx,
                "outcome": {"questionId": qid, "value": choices[qid]},
            }
        )
        offset += 1

trajectory.append(
    {
        "timestamp": _ts(base, offset),
        "actor": "system",
        "action": "survey_completed",
        "context": {"instrumentId": instrument_id},
        "outcome": {
            "numAnswered": len(answers),
            "missingRequiredQuestionIds": [],
            "valid": True,
        },
    }
)

payload = {
    "instrument": {
        "id": instrument_id,
        "title": str(instrument.get("title") or "Household Financial Pressure"),
    },
    "answers": answers,
    "trajectory": trajectory,
}
OUTPUT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
PY
