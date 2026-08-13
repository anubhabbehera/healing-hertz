from typing import Literal

from pydantic import BaseModel, Field


class AdviceItem(BaseModel):
    priority: int = Field(description="1 = do first")
    title: str
    related_rule_ids: list[str] = Field(
        default_factory=list, description="rule_ids of findings this remediates"
    )
    rationale: str = Field(description="Why this matters in this network's specific context")
    steps: list[str] = Field(description="Concrete UniFi UI/CLI steps")
    effort: Literal["low", "medium", "high"]


class AdvicePlan(BaseModel):
    overall_assessment: str
    items: list[AdviceItem]
    quick_wins: list[str] = Field(default_factory=list)
