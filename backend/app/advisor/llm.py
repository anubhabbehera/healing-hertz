from __future__ import annotations

import json
import logging

import anthropic
from pydantic import ValidationError

from app.collectors.snapshot import Snapshot
from app.config import Settings
from app.rules.base import Finding, RunHistory

from .prompts import SYSTEM_PROMPT, build_payload
from .schema import AdvicePlan

logger = logging.getLogger(__name__)

# Reasoning models routed through gateways can take a while on large scans.
ADVISOR_TIMEOUT_SEC = 120.0


def _user_message(payload: str) -> str:
    return "Diagnostic scan digest follows. Produce the remediation plan.\n\n" + payload


async def _parse_structured(
    client: anthropic.AsyncAnthropic, settings: Settings, payload: str
) -> AdvicePlan | None:
    """Official path: schema-enforced structured output."""
    response = await client.messages.parse(
        model=settings.advisor_model,
        max_tokens=settings.advisor_max_tokens,
        system=[{
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[{"role": "user", "content": _user_message(payload)}],
        output_format=AdvicePlan,
    )
    return response.parsed_output


async def _plain_json_fallback(
    client: anthropic.AsyncAnthropic, settings: Settings, payload: str
) -> AdvicePlan | None:
    """Compatibility path for Anthropic-compatible gateways (OpenRouter, LiteLLM, ...)
    that don't support the structured-output parameter: embed the schema in the
    prompt, request bare JSON, validate locally."""
    schema = json.dumps(AdvicePlan.model_json_schema())
    response = await client.messages.create(
        model=settings.advisor_model,
        max_tokens=settings.advisor_max_tokens,
        system=(
            SYSTEM_PROMPT
            + "\n\nRespond with a single JSON object matching this JSON Schema, and "
            "nothing else (no prose, no markdown fences):\n"
            + schema
        ),
        messages=[{"role": "user", "content": _user_message(payload)}],
    )
    text = next((b.text for b in response.content if b.type == "text"), "")
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        logger.warning("Advisor fallback returned no JSON object")
        return None
    try:
        return AdvicePlan.model_validate_json(text[start : end + 1])
    except ValidationError as exc:
        logger.warning("Advisor fallback JSON failed validation: %s", exc)
        return None


async def generate_advice(
    findings: list[Finding],
    snapshot: Snapshot,
    history: RunHistory,
    settings: Settings,
) -> tuple[AdvicePlan | None, str, str | None]:
    """Returns (plan, status, error_detail); status is ok | skipped | failed."""
    if not settings.anthropic_api_key:
        return None, "skipped", None

    payload = build_payload(findings, snapshot, history)
    client = anthropic.AsyncAnthropic(
        api_key=settings.anthropic_api_key,
        base_url=settings.anthropic_base_url or None,
        timeout=ADVISOR_TIMEOUT_SEC,
    )
    try:
        if settings.anthropic_base_url:
            # Gateways vary: some reject Anthropic's structured-output parameter,
            # others silently ignore it. Try it, then degrade to prompt-based JSON
            # on any failure.
            try:
                plan = await _parse_structured(client, settings, payload)
            except Exception as exc:  # noqa: BLE001 — gateway quirks take many shapes
                logger.info("Structured output failed on gateway (%s); using JSON fallback", exc)
                plan = None
            if plan is None:
                plan = await _plain_json_fallback(client, settings, payload)
        else:
            plan = await _parse_structured(client, settings, payload)

        if plan is None:
            logger.warning("Advisor returned unparsable output")
            return None, "failed", (
                "The model's response was not valid JSON matching the plan schema "
                "(often output truncation — try a larger ADVISOR_MAX_TOKENS or a "
                "different ADVISOR_MODEL)."
            )
        return plan, "ok", None
    except anthropic.APIError as exc:
        logger.warning("Advisor call failed: %s", exc)
        detail = f"{type(exc).__name__}: {str(exc)[:200]}"
        if isinstance(exc, anthropic.APITimeoutError):
            detail = (
                f"Timed out after {ADVISOR_TIMEOUT_SEC:.0f}s — the model was too slow "
                "for this payload; try a faster ADVISOR_MODEL."
            )
        return None, "failed", detail
    finally:
        await client.close()
