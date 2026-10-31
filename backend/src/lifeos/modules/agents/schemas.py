"""Coordinated agents (task 28, UC03 « achat important »): question, findings, synthesis."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

StepAgent = Literal["finance", "coaching", "core"]
FindingAgent = Literal["finance", "coaching"]
Confidence = Literal["faible", "moyenne", "élevée"]
Verdict = Literal["raisonnable", "à reporter", "déconseillé", "incertain"]


class PurchaseQuestion(BaseModel):
    space_id: uuid.UUID
    # Exact decimals, euros only (like the budget of the month).
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    label: str = Field(min_length=1, max_length=120)


class Figure(BaseModel):
    """A figure computed locally by one agent, with what it rests on."""

    key: str
    agent: FindingAgent
    label: str
    value: Decimal
    unit: Literal["EUR", "%", "mois"]
    low: Decimal | None = None
    high: Decimal | None = None
    basis: str


class GoalFinding(BaseModel):
    """An active goal of the space, as the coaching agent read it."""

    key: str
    agent: FindingAgent = "coaching"
    goal_id: uuid.UUID
    title: str
    progress: int | None
    progress_basis: Literal["metric", "milestones", "none"]
    due_date: date | None
    # Counted in money (a savings goal, a goal in euros): the purchase competes with it.
    financial: bool
    current: Decimal | None = None
    target: Decimal | None = None
    unit: str | None = None
    remaining: Decimal | None = None
    monthly_contribution: Decimal | None = None
    # Months of planned contributions the purchase amounts to.
    purchase_months: Decimal | None = None


class Hypothesis(BaseModel):
    agent: StepAgent
    text: str


class StepOut(BaseModel):
    """What one agent did: under its own permissions, on which data."""

    agent: StepAgent
    label: str
    role: str
    resources_read: list[str]
    missing: list[str]
    items: int
    cloud_calls: int = 0
    duration_ms: int


class AdvicePoint(BaseModel):
    text: str = Field(default="", max_length=400)
    refs: list[str] = Field(
        default_factory=list,
        max_length=6,
        description="Clés des chiffres (F1, F2…) et des objectifs (G1, G2…) utilisés",
    )


class PurchaseAdvice(BaseModel):
    """What the model may write. Every reason must cite the keys of the data it uses."""

    verdict: Verdict = "incertain"
    confidence: Confidence = "faible"
    recommendation: str = Field(
        default="", max_length=1200, description="Recommandation en 2 à 5 phrases"
    )
    reasons: list[AdvicePoint] = Field(default_factory=list, max_length=5)
    hypotheses: list[str] = Field(default_factory=list, max_length=5)
    uncertainties: list[str] = Field(default_factory=list, max_length=5)


class ReasonOut(BaseModel):
    text: str
    refs: list[str]
    # The agents whose findings the reason cites (from its keys, never from the model).
    agents: list[FindingAgent]
    # False: the reason cites no figure or goal actually computed.
    supported: bool


class AdviceOut(BaseModel):
    verdict: Verdict
    confidence: Confidence
    recommendation: str
    reasons: list[ReasonOut]
    hypotheses: list[str]
    uncertainties: list[str]
    # Keys cited by the model that no agent computed (dropped from the reasons).
    unknown_refs: list[str] = []
    # Amounts (€, %) written by the model that match no computed figure.
    unmatched_amounts: list[str] = []


class RunError(BaseModel):
    code: str
    title: str
    detail: str | None = None
    missing: list[str] = []


class PurchaseResult(BaseModel):
    run_id: uuid.UUID
    space_id: uuid.UUID
    created_at: datetime
    question: PurchaseQuestion
    steps: list[StepOut]
    figures: list[Figure]
    goals: list[GoalFinding]
    hypotheses: list[Hypothesis]
    advice: AdviceOut | None
    error: RunError | None
    cloud_calls: int
    max_cloud_calls: int
