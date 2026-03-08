"""
schemas/agents.py

Pydantic models that define:
  1. The JSON schema sent to Vertex AI as responseSchema (structured output)
  2. The validated Python objects returned from each agent

Why two representations of the same shape?
  - Pydantic models   → used by Python code to validate + work with the data
  - Plain dict schemas → sent to Vertex AI so the model knows what to return

All agent outputs are validated via model.model_validate() in agents/base.py
before being stored in Firestore. If validation fails, the orchestrator raises
immediately — no silent bad data gets written.
"""

from pydantic import BaseModel, Field
from typing import Optional


# ---------------------------------------------------------------------------
# FraudRiskAgent output
# ---------------------------------------------------------------------------

class FraudRiskOutput(BaseModel):
    """
    Risk assessment for an incoming WooCommerce order.

    Fields:
        risk_level  One of: low | medium | high
        confidence  Model's self-reported confidence (0.0 to 1.0)
        reasons     List of human-readable signals that drove the decision.
                    Must have at least one entry.
    """
    risk_level: str   = Field(..., description="low | medium | high")
    confidence: float = Field(..., ge=0.0, le=1.0)
    reasons:    list[str] = Field(..., min_length=1)


# ---------------------------------------------------------------------------
# SupportReplyAgent output
# ---------------------------------------------------------------------------

class SupportReplyOutput(BaseModel):
    """
    Draft customer-facing support email for the order.

    Fields:
        subject      Email subject line.
        body         Full email body text.
        tone         One of: friendly | professional | urgent
        disclaimers  List of standard legal/ops disclaimers to append.
                     Can be empty list [] if none are needed.
    """
    subject:     str
    body:        str
    tone:        str       = Field(..., description="friendly | professional | urgent")
    disclaimers: list[str]


# ---------------------------------------------------------------------------
# FulfillmentNoteAgent output
# ---------------------------------------------------------------------------

class FulfillmentNoteOutput(BaseModel):
    """
    Internal fulfillment instructions for the warehouse/ops team.

    Fields:
        priority      One of: standard | expedited | hold
        packing_notes Instructions for packing the order.
        carrier_hint  Suggested carrier (e.g. "USPS", "FedEx").
                      Optional — None if not determinable from the order.
    """
    priority:     str            = Field(..., description="standard | expedited | hold")
    packing_notes: str
    carrier_hint: Optional[str]  = None


# ---------------------------------------------------------------------------
# Usage metadata
# Aggregated across all agents in one orchestrator run.
# Stored in BigQuery usage_ledger and returned in API responses.
# ---------------------------------------------------------------------------

class UsageMetadata(BaseModel):
    """
    Token counts and estimated cost for one pipeline run.
    Accumulated across all 3 agent calls by the orchestrator.
    """
    model:             str   = ""
    prompt_tokens:     int   = 0
    output_tokens:     int   = 0
    total_tokens:      int   = 0
    prompt_chars:      int   = 0
    output_chars:      int   = 0
    estimated_cost_usd: float = 0.0


# ---------------------------------------------------------------------------
# Orchestrator result envelope
# Returned by run_orchestrator() and consumed by main.py webhook handler.
# ---------------------------------------------------------------------------

class OrchestratorResult(BaseModel):
    """
    The complete output of one orchestrator run.

    agent_outputs  Dict keyed by agent name, each value is the validated
                   agent output as a plain dict (ready to store in Firestore).
                   Keys: "fraud_risk", "support_reply", "fulfillment_note"

    usage          Accumulated token + cost metadata across all 3 agents.
    """
    agent_outputs: dict  # { agent_name: validated_output_dict }
    usage:         UsageMetadata


# ---------------------------------------------------------------------------
# Vertex AI response schemas
#
# These are plain Python dicts that mirror the Pydantic models above.
# They are passed directly to GenerationConfig(response_schema=...) so that
# Vertex AI knows what JSON shape to produce.
#
# Rule: every field marked "required" in Pydantic must also be in "required"
# here. Optional fields (like carrier_hint) should be omitted from "required".
# ---------------------------------------------------------------------------

FRAUD_RISK_SCHEMA = {
    "type": "object",
    "properties": {
        "risk_level": {
            "type": "string",
            "enum": ["low", "medium", "high"],
        },
        "confidence": {
            "type": "number",
        },
        "reasons": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": ["risk_level", "confidence", "reasons"],
}


SUPPORT_REPLY_SCHEMA = {
    "type": "object",
    "properties": {
        "subject": {
            "type": "string",
        },
        "body": {
            "type": "string",
        },
        "tone": {
            "type": "string",
            "enum": ["friendly", "professional", "urgent"],
        },
        "disclaimers": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": ["subject", "body", "tone", "disclaimers"],
}


FULFILLMENT_NOTE_SCHEMA = {
    "type": "object",
    "properties": {
        "priority": {
            "type": "string",
            "enum": ["standard", "expedited", "hold"],
        },
        "packing_notes": {
            "type": "string",
        },
        "carrier_hint": {
            "type": "string",
            # Not in "required" — model can omit this field
        },
    },
    "required": ["priority", "packing_notes"],
    # carrier_hint is intentionally absent from required
}