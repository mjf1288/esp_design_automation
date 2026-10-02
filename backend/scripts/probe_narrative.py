#!/usr/bin/env python3
"""Probe the narrative agent against a real LLM.

Purpose: determine whether guard refusals are catching genuine hallucination or
are an artifact of an over-strict guard. This distinction matters — a guard that
always refuses is indistinguishable from a broken feature, and would push a team
toward loosening it for the wrong reason.

Optional platform-only diagnostic, not needed to run the local demo or tests.
It requires a separately available platform LLM SDK and your own authorized
PPLX_LLM_API_ADDRESS / PPLX_LLM_API_KEY environment values. No credentials are
included. The local demo keeps external narrative generation disabled.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

from esp_agents.llm import PplxStructuredLLM
from esp_agents.narrative import NarrativeAgent
from esp_engine.pipeline import DesignEngine
from test_pipeline import _base_case


def main() -> int:
    result = DesignEngine().run(_base_case())
    print(f"verdict={result.verdict.value} candidates={len(result.candidates)}")

    agent = NarrativeAgent(llm=PplxStructuredLLM())
    outcome = agent.run(result)
    print(f"\nstatus={outcome.status} attempts={outcome.attempts}")

    if outcome.status == "ok":
        narrative = outcome.narrative
        print("\n--- fact_summary ---")
        print(narrative.fact_summary)
        print("\n--- configuration_facts ---")
        for line in narrative.configuration_facts:
            print(f"  - {line}")
        print("\n--- validity_boundary_facts ---")
        for line in narrative.validity_boundary_facts:
            print(f"  - {line}")
        print("\n--- watch_facts ---")
        for line in narrative.watch_facts:
            print(f"  - {line}")
        print("\n--- judgments ---")
        for judgment in narrative.judgments:
            print(f"  - [{judgment.confidence}] {judgment.statement}")
        return 0

    print(f"\nrefusal_reason: {outcome.refusal_reason}")
    print("\nviolations that caused the refusal:")
    for violation in outcome.numeric_violations:
        print(f"  token={violation.token!r} value={violation.value}")
        print(f"    {violation.context}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
