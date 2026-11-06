"""Score Surge API — the doorway the iPhone app will knock on. Started 5 Nov 2026.

The website (app.py, Streamlit) does not use this and is not changed by it. This is
a separate small server that runs the SAME Python logic for the phone app, so the
math and the accuracy rules exist in exactly one place.

First endpoint: the FMS calculator. It is pure math from fms_core.py — no login,
no AI, no database, no secrets — which makes it the safest piece to start with.

Run locally:
    pip install -r api/requirements.txt
    uvicorn api.main:app --reload

Rules for every endpoint added here later (read before adding one):
  * Never re-write logic that already lives in a shared module. Import it.
  * AI-written questions go through the same order the website uses: save as
    unverified -> ground_exam_questions() -> show only grounded rows -> count usage.
  * The Anthropic key stays on this server. The phone app never holds a secret.
  * Anything that costs AI or shows a paid feature checks the sailor's Supabase
    login and tier first, using the same database rules the website relies on.
"""

from __future__ import annotations

import os
import sys
from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel, Field, model_validator

# The shared modules live in the repo root, next to app.py.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fms_core import EXAM_MAX, FMS_RULES, compute_fms  # noqa: E402

API_VERSION = "0.1.0"

app = FastAPI(
    title="Score Surge API",
    version=API_VERSION,
    description="Backend for the Score Surge iPhone app. Same logic as the website.",
)


@app.get("/health")
def health() -> dict:
    """A quick 'is the server up' check for hosting and for the phone app."""
    return {"ok": True, "version": API_VERSION}


class FmsRequest(BaseModel):
    """What the phone sends. Same inputs as the website's FMS tab.

    The limits are the website form's own limits (app.py, FMS tab), so the phone
    can't send a number the website would never accept. Within them, compute_fms()
    applies the chart's caps exactly as it does on the website.
    """

    paygrade: Literal["E4", "E5", "E6", "E7"]
    exam_score: float = Field(ge=0, le=EXAM_MAX, description="Exam standard score")
    pma: float = Field(ge=0, description="PMA (E4/E5, max 4.00) or RSCA PMA (E6/E7, max 5.80)")
    years_in_rate: float = Field(ge=0, le=30, description="Service in paygrade, years")
    awards: float = Field(default=0, ge=0, description="Awards points (paygrade cap)")
    education: Literal[0, 2, 4] = Field(default=0, description="0 none, 2 AA/AS, 4 BA/BS+")
    pna: float = Field(default=0, ge=0, le=9)

    @model_validator(mode="after")
    def within_paygrade_limits(self):
        r = FMS_RULES[self.paygrade]
        if self.pma > r["pma_input_max"]:
            raise ValueError(f"{r['pma_name']} can't be above {r['pma_input_max']:.2f} for {self.paygrade}")
        if self.awards > max(r["awards_max"], 0.5):
            raise ValueError(f"Awards can't be above {r['awards_max']:.0f} points for {self.paygrade}")
        return self


class FmsResponse(BaseModel):
    paygrade: str
    total: float
    fms_max: float
    breakdown: dict[str, float]
    pma_name: str


@app.post("/v1/fms", response_model=FmsResponse)
def fms(req: FmsRequest) -> FmsResponse:
    """Final Multiple Score from the official chart (fms_core.compute_fms)."""
    total, parts = compute_fms(
        req.paygrade, req.exam_score, req.pma, req.years_in_rate,
        req.awards, req.education, req.pna,
    )
    rules = FMS_RULES[req.paygrade]
    return FmsResponse(
        paygrade=req.paygrade,
        total=total,
        fms_max=rules["fms_max"],
        breakdown=parts,
        pma_name=rules["pma_name"],
    )
