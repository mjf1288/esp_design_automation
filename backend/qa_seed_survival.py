"""Seed an isolated perimeter solely for visual QA of the live survival endpoint."""
from __future__ import annotations

import json
from datetime import date, timedelta
from urllib.request import Request, urlopen


BASE = "http://127.0.0.1:8000/api/empirical/observations"
OPERATOR = "qa-survival"
INSTALL = date(2024, 1, 1)

# Six failures and four right-censored observations at distinct run lives.  The
# UI must show drops only at the former, and the latter as censor marks.
ROWS = (
    (120, True), (155, False), (170, True), (220, False), (240, True),
    (290, False), (310, True), (390, True), (430, False), (450, False),
)


def post(days: int, failed: bool, index: int) -> None:
    observed = (INSTALL + timedelta(days=days)).isoformat()
    payload = {
        "external_case_id": f"qa-survival-{index:02d}",
        "pump_model": "RC2500",
        "case_inputs_snapshot": {"purpose": "isolated visual QA only"},
        "selected_configuration": {"pump_id": "slb-reda-rc2500", "stages": 41},
        "install_date": INSTALL.isoformat(),
        "pull_date": observed if failed else None,
        "still_running": not failed,
        "outcome_observed_date": observed,
        "is_failure": failed,
        "failure_mode": "qa_visual_failure" if failed else None,
        "operating_conditions": {},
        "source": {
            "value": "Isolated QA historical record",
            "unit": None,
            "source": "report",
            "confidence": 0.9,
        },
        "engineer_commentary": "Scratch perimeter record for live Kaplan–Meier visual QA.",
    }
    request = Request(
        BASE,
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            # Two-level perimeter headers (§9.2): run-life history is inner-capsule
            # data, so it is addressed as an operator under the demo org.
            "X-Org-Id": "demo",
            "X-Operator-Id": OPERATOR,
        },
        method="POST",
    )
    with urlopen(request, timeout=10) as response:
        if response.status != 201:
            raise RuntimeError(f"Expected 201, received {response.status}")


for number, (days, failed) in enumerate(ROWS, start=1):
    post(days, failed, number)

print(f"Seeded {len(ROWS)} isolated observations for perimeter demo/{OPERATOR}.")
