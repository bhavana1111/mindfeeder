"""
agents/orchestrator.py

Orchestrator — the supervisor that coordinates all three specialist agents.

What it does:
  Receives the raw order payload from main.py, decides which agents to run,
  calls each one in sequence, accumulates token usage across all calls,
  and returns a single OrchestratorResult containing all outputs and
  combined usage metadata.

Pattern: Supervisor + Workers
  The orchestrator is the supervisor. The three specialist agents are workers.
  The supervisor knows about all workers. Workers know nothing about each other.

  Orchestrator
      ├── FraudRiskAgent      → { risk_level, confidence, reasons[] }
      ├── SupportReplyAgent   → { subject, body, tone, disclaimers[] }
      └── FulfillmentNoteAgent → { priority, packing_notes, carrier_hint }

Routing logic (current):
  Always run all three agents for every order. This is the simplest and
  most predictable behaviour. See "Future routing ideas" below for how
  to extend this if needed.

Future routing ideas (not implemented — documented for reference):
  - Skip FraudRiskAgent for orders under $10 (low risk, save tokens)
  - Skip SupportReplyAgent if event_type is NOT "order.created"
  - Set FulfillmentNoteAgent priority="hold" based on FraudRiskAgent output
    (requires passing fraud_output as extra param — see note in fulfillment_note.py)
  - Run agents in parallel using asyncio.gather() for lower latency
    (safe to do since agents are independent and don't share state)

Usage accumulation:
  Each agent call returns its own UsageMetadata. The orchestrator adds
  tokens and cost across all three so main.py receives one combined total
  to write to BigQuery. This means one BQ row per pipeline run, not three.
"""

import json
import logging

from agents.fraud_risk import run_fraud_risk_agent
from agents.support_reply import run_support_reply_agent
from agents.fulfillment_note import run_fulfillment_note_agent
from schemas.agents import OrchestratorResult, UsageMetadata

logger = logging.getLogger("mindfeeder.orchestrator")


# ---------------------------------------------------------------------------
# Main orchestrator function
# ---------------------------------------------------------------------------

async def run_orchestrator(
    payload: dict,
    event_id: str,
) -> OrchestratorResult:
    """
    Coordinate all specialist agents for a WooCommerce order event.

    Parameters:
        payload   The full webhook payload dict. The orchestrator extracts
                  payload["order"] and passes it to each specialist agent.
        event_id  The event UUID. Passed through to every agent so all
                  Vertex AI calls for this event share the same trace ID
                  in Cloud Logging.

    Returns:
        OrchestratorResult containing:
          - agent_outputs: dict of validated agent output dicts
          - usage: combined UsageMetadata across all agent calls

    Raises:
        Any exception from a specialist agent propagates up to main.py.
        The webhook handler catches it, marks the event as "failed" in
        Firestore, and returns HTTP 500 to the caller.
        There is intentionally no try/except here — fail fast and loud
        is better than silently continuing with partial results.
    """

    # Extract the order object from the payload
    # All three agents receive the same order dict
    order = payload.get("order", {})

    # Initialise accumulators
    accumulated = UsageMetadata(model="")
    outputs = {}

    # ── Step 1: FraudRiskAgent ────────────────────────────────────────────────
    # Runs first so its result could theoretically inform the other agents
    # in a future version (e.g. pass risk_level to FulfillmentNoteAgent).
    logger.info(json.dumps({
        "message":  "Starting FraudRiskAgent",
        "event_id": event_id,
    }))

    fraud_output, fraud_usage = await run_fraud_risk_agent(order, event_id)
    outputs["fraud_risk"] = fraud_output.model_dump()
    _accumulate(accumulated, fraud_usage)

    logger.info(json.dumps({
        "message":    "FraudRiskAgent complete",
        "event_id":   event_id,
        "risk_level": fraud_output.risk_level,
        "confidence": fraud_output.confidence,
        "tokens":     fraud_usage.total_tokens,
    }))

    # ── Step 2: SupportReplyAgent ─────────────────────────────────────────────
    # Drafts the customer email. Independent of fraud output.
    logger.info(json.dumps({
        "message":  "Starting SupportReplyAgent",
        "event_id": event_id,
    }))

    support_output, support_usage = await run_support_reply_agent(order, event_id)
    outputs["support_reply"] = support_output.model_dump()
    _accumulate(accumulated, support_usage)

    logger.info(json.dumps({
        "message":  "SupportReplyAgent complete",
        "event_id": event_id,
        "tone":     support_output.tone,
        "tokens":   support_usage.total_tokens,
    }))

    # ── Step 3: FulfillmentNoteAgent ──────────────────────────────────────────
    # Generates warehouse instructions. Independent of other agent outputs.
    #
    # Future improvement: pass fraud_output.risk_level here so the agent
    # can set priority="hold" when fraud risk is "high". Currently the
    # fulfillment agent sets hold based on order signals alone.
    logger.info(json.dumps({
        "message":  "Starting FulfillmentNoteAgent",
        "event_id": event_id,
    }))

    fulfill_output, fulfill_usage = await run_fulfillment_note_agent(order, event_id)
    outputs["fulfillment_note"] = fulfill_output.model_dump()
    _accumulate(accumulated, fulfill_usage)

    logger.info(json.dumps({
        "message":      "FulfillmentNoteAgent complete",
        "event_id":     event_id,
        "priority":     fulfill_output.priority,
        "carrier_hint": fulfill_output.carrier_hint,
        "tokens":       fulfill_usage.total_tokens,
    }))

    # ── Final summary log ─────────────────────────────────────────────────────
    logger.info(json.dumps({
        "message":             "All agents complete",
        "event_id":            event_id,
        "agents_run":          list(outputs.keys()),
        "total_tokens":        accumulated.total_tokens,
        "estimated_cost_usd":  accumulated.estimated_cost_usd,
        "fraud_risk":          fraud_output.risk_level,
        "fulfillment_priority": fulfill_output.priority,
    }))

    return OrchestratorResult(
        agent_outputs=outputs,
        usage=accumulated,
    )


# ---------------------------------------------------------------------------
# Usage accumulator helper
# ---------------------------------------------------------------------------

def _accumulate(acc: UsageMetadata, new: UsageMetadata):
    """
    Merge a single agent's UsageMetadata into the running total.

    Called once per agent after each successful run.
    Mutates acc in place — no return value.

    Note on model field:
        All three agents use the same VERTEX_MODEL value from base.py,
        so acc.model ends up as that model string after the last agent runs.
        If agents ever use different models, this would need to be a list.
    """
    acc.model          =  new.model   # last agent wins — all same model anyway
    acc.prompt_tokens  += new.prompt_tokens
    acc.output_tokens  += new.output_tokens
    acc.total_tokens   += new.total_tokens
    acc.prompt_chars   += new.prompt_chars
    acc.output_chars   += new.output_chars
    acc.estimated_cost_usd += new.estimated_cost_usd