"""
agents/base.py

Shared helper for calling Vertex AI with structured JSON output.

Key decisions:
  - responseMimeType: "application/json" forces the model to emit valid JSON
  - responseSchema: the schema dict from schemas/agents.py is passed directly
  - We parse usageMetadata from the response for cost tracking
  - Cost formula uses gemini-1.5-flash character-based pricing (configurable)
  - Pydantic validation happens here; raises ValueError on schema mismatch

Cost assumptions (document in README):
  Gemini 1.5 Flash (as of early 2025):
    Input:  $0.000125 / 1K chars
    Output: $0.000375 / 1K chars
  These are approximations. Set VERTEX_COST_INPUT_PER_1K_CHARS and
  VERTEX_COST_OUTPUT_PER_1K_CHARS env vars to override.
"""

import json
import logging
import os
from typing import Type

import vertexai
from vertexai.generative_models import GenerativeModel, GenerationConfig
from pydantic import BaseModel

from schemas.agents import UsageMetadata

logger = logging.getLogger("mindfeeder.base_agent")

# ---------------------------------------------------------------------------
# Config — all overridable via environment variables
# ---------------------------------------------------------------------------
VERTEX_MODEL    = os.environ.get("VERTEX_MODEL", "gemini-2.0-flash-001")
VERTEX_PROJECT  = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
VERTEX_LOCATION = os.environ.get("VERTEX_LOCATION", "us-central1")

# Cost per 1K characters (Gemini 1.5 Flash approximate rates)
COST_INPUT_PER_1K  = float(os.environ.get("VERTEX_COST_INPUT_PER_1K_CHARS", "0.000125"))
COST_OUTPUT_PER_1K = float(os.environ.get("VERTEX_COST_OUTPUT_PER_1K_CHARS", "0.000375"))

# Initialise Vertex AI SDK once at import time (not per-call)
vertexai.init(project=VERTEX_PROJECT, location=VERTEX_LOCATION)


# ---------------------------------------------------------------------------
# Main function
# ---------------------------------------------------------------------------

async def call_vertex_structured(
    prompt: str,
    response_schema: dict,
    output_model: Type[BaseModel],
    agent_name: str,
    event_id: str,
) -> tuple[BaseModel, UsageMetadata]:
    """
    Call Vertex AI and return a validated structured output + usage metadata.

    Parameters:
        prompt          The full prompt string to send to the model.
        response_schema The JSON schema dict (from schemas/agents.py) that tells
                        Vertex AI exactly what shape to return.
        output_model    The Pydantic class to validate the response against.
        agent_name      Name used in log entries (e.g. "fraud_risk").
        event_id        Event UUID — included in every log entry for tracing.

    Returns:
        (validated_output, usage_metadata)
        validated_output is an instance of output_model.
        usage_metadata contains token counts and estimated cost.

    Raises:
        ValueError  If the model response fails Pydantic validation.
        Exception   For any Vertex API error (network, quota, etc).
                    Callers should handle retries if needed.
    """

    # ── Build the model and generation config ────────────────────────────────
    model = GenerativeModel(VERTEX_MODEL)

    generation_config = GenerationConfig(
        response_mime_type="application/json",  # forces model to emit JSON
        response_schema=response_schema,        # constrains the JSON shape
        temperature=0.2,     # low temperature = more consistent structured output
        max_output_tokens=1024,
    )

    # ── Log before calling (useful for debugging slow/failed calls) ──────────
    logger.info(json.dumps({
        "message":      "Calling Vertex AI",
        "agent":        agent_name,
        "event_id":     event_id,
        "model":        VERTEX_MODEL,
        "prompt_chars": len(prompt),
    }))

    # ── Make the API call ────────────────────────────────────────────────────
    response = model.generate_content(
        prompt,
        generation_config=generation_config,
    )

    raw_text = response.text

    logger.info(json.dumps({
        "message":      "Vertex AI response received",
        "agent":        agent_name,
        "event_id":     event_id,
        "output_chars": len(raw_text),
    }))

    # ── Validate the response against the Pydantic model ────────────────────
    # If the model returns something that doesn't match the schema,
    # we raise immediately — no silent bad data gets stored.
    try:
        parsed    = json.loads(raw_text)
        validated = output_model.model_validate(parsed)
    except Exception as exc:
        logger.error(json.dumps({
            "message":    "Agent output validation failed",
            "agent":      agent_name,
            "event_id":   event_id,
            "raw_output": raw_text[:500],  # first 500 chars for debugging
            "error":      str(exc),
        }))
        raise ValueError(f"[{agent_name}] validation failed: {exc}") from exc

    # ── Extract usage metadata for cost tracking ─────────────────────────────
    # usageMetadata is available on the response object.
    # Use getattr with fallback to 0 — some model versions omit these fields.
    usage_meta    = response.usage_metadata
    prompt_tokens = getattr(usage_meta, "prompt_token_count",     0) or 0
    output_tokens = getattr(usage_meta, "candidates_token_count", 0) or 0
    prompt_chars  = len(prompt)
    output_chars  = len(raw_text)

    # Cost formula: character-based (Vertex AI bills some SKUs by character)
    # Formula: (chars / 1000) * rate_per_1k_chars
    estimated_cost = (
        (prompt_chars  / 1000) * COST_INPUT_PER_1K
        + (output_chars / 1000) * COST_OUTPUT_PER_1K
    )

    usage = UsageMetadata(
        model=VERTEX_MODEL,
        prompt_tokens=prompt_tokens,
        output_tokens=output_tokens,
        total_tokens=prompt_tokens + output_tokens,
        prompt_chars=prompt_chars,
        output_chars=output_chars,
        estimated_cost_usd=round(estimated_cost, 8),
    )

    return validated, usage