"""
agents/fulfillment_note.py

FulfillmentNoteAgent — generates internal warehouse/ops instructions for an order.

What it does:
  Sends the order details to Vertex AI with a structured prompt that asks
  the model to write concise internal fulfillment instructions. Returns a
  validated FulfillmentNoteOutput object containing a priority level,
  packing notes for the warehouse team, and an optional carrier hint.

Output shape:
  {
    "priority":     "standard" | "expedited" | "hold",
    "packing_notes": "Pack 2x ABC-1 securely. Fragile label not required.",
    "carrier_hint":  "USPS" | "FedEx" | "UPS" | null
  }

Priority selection logic (prompt-guided):
  - "standard"   Normal order, no urgency signals, no fraud concerns.
  - "expedited"  Customer note requests fast delivery ("ASAP", "tomorrow").
  - "hold"       Fraud risk signals present or payment needs verification.

Carrier hint logic (prompt-guided):
  - US domestic low weight  → USPS
  - US domestic high value  → FedEx or UPS (better tracking + insurance)
  - International           → FedEx International or DHL
  - Unknown / undetectable  → null (do not guess)

Note on the "hold" priority:
  This agent does not have direct access to the FraudRiskAgent output.
  The orchestrator runs agents sequentially but does not pass outputs
  between them. "hold" is therefore set based on order signals alone
  (very high total, suspicious note pattern), not on the fraud score.
  A future improvement would be to pass fraud_output into this agent
  so it can set "hold" when risk_level is "high".
"""

import json

from agents.base import call_vertex_structured
from schemas.agents import FulfillmentNoteOutput, FULFILLMENT_NOTE_SCHEMA, UsageMetadata


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------
# This prompt is intentionally written in warehouse/ops language, not
# customer-facing language. Short sentences. Imperative tone. Practical.
#
# Key prompt engineering decisions:
#   1. Role is "fulfillment operations coordinator" — shifts model register
#      to internal/ops rather than customer-facing
#   2. Priority rules are tied to specific observable signals in the order
#   3. Packing notes are constrained to 1-2 sentences — warehouse staff
#      need brevity, not essays
#   4. Carrier hint rules are explicit — prevents model guessing randomly
#   5. "Do not recommend holds without clear justification" — avoids
#      over-flagging normal orders as holds
# ---------------------------------------------------------------------------

PROMPT_TEMPLATE = """
You are a fulfillment operations coordinator at an e-commerce warehouse.
Generate concise internal fulfillment instructions for the following order.
These notes are for warehouse staff, not for the customer.

Order details:
{order_json}

Instructions:

1. PRIORITY LEVEL
   Set priority based on these rules in order of precedence:

   "hold" — use ONLY if:
     - Order total is over $500 AND customer note contains urgency language
       (this combination is a fraud indicator worth pausing for review)
     - Customer note explicitly requests something impossible or suspicious
       (e.g. "ship to a different address than billing")
     - Do NOT set hold for high totals alone. Do NOT over-flag.

   "expedited" — use if:
     - customer_note contains urgency language: "ASAP", "urgent", "tomorrow",
       "need it by", "leaving", "rush", "as soon as possible"
     - These customers expect fast handling. Expedited means pick/pack today.

   "standard" — use for everything else.
     - This is the default. Most orders are standard.

2. PACKING NOTES
   Write 1-2 sentences of practical instructions for the warehouse team.
   Include:
     - Item count and SKU (e.g. "Pick 2x ABC-1")
     - Any fragility concern if item name or category suggests it
     - Any special handling if qty is unusually high
     - Whether to include a packing slip (always yes unless order note says no)
   Keep it under 40 words. No fluff. Warehouse staff are busy.

   Examples of good packing notes:
     "Pick 2x ABC-1. Double-box due to quantity. Include packing slip."
     "Pick 1x XYZ-9. Standard packaging. Include packing slip."
     "Pick 3x DEF-2. High value — use bubble wrap. Include packing slip."

3. CARRIER HINT
   Suggest a carrier based on destination and order value.
   Use ONLY these values or null:

     "USPS"              US domestic, order total under $100
     "UPS"               US domestic, order total $100-$300
     "FedEx"             US domestic, order total over $300 (better insurance)
     "FedEx International"  Non-US destination
     "DHL"               Non-US destination (alternative to FedEx Intl)

   If destination country is missing or unclear, return null.
   Do not guess. Null is better than a wrong carrier suggestion.

Return ONLY valid JSON matching the schema. No extra commentary.
These notes go directly into the warehouse management system.
"""


# ---------------------------------------------------------------------------
# Agent function
# ---------------------------------------------------------------------------

async def run_fulfillment_note_agent(
    order: dict,
    event_id: str,
) -> tuple[FulfillmentNoteOutput, UsageMetadata]:
    """
    Run the FulfillmentNoteAgent for a single WooCommerce order.

    Parameters:
        order     The order dict from the webhook payload.
                  Expected keys: id, email, total, currency, items,
                  shipping_address, customer_note.
        event_id  The event UUID — passed through to base.py for log tracing.

    Returns:
        (FulfillmentNoteOutput, UsageMetadata)
        FulfillmentNoteOutput is already Pydantic-validated before being returned.
        UsageMetadata contains token counts + estimated cost for this agent call.

    Raises:
        ValueError   If Vertex AI returns JSON that fails Pydantic validation.
        Exception    For any Vertex API error (network, quota exceeded, etc).

    Design note:
        This agent has no visibility into the FraudRiskAgent output.
        The orchestrator runs agents sequentially but does not share outputs
        between them. If you want this agent to set priority="hold" based on
        the fraud score, pass fraud_output as an additional parameter and
        inject it into the prompt. For now, hold is set on order signals alone.
    """

    # Inject the order data into the prompt
    prompt = PROMPT_TEMPLATE.format(
        order_json=json.dumps(order, indent=2)
    )

    return await call_vertex_structured(
        prompt=prompt,
        response_schema=FULFILLMENT_NOTE_SCHEMA,
        output_model=FulfillmentNoteOutput,
        agent_name="fulfillment_note",
        event_id=event_id,
    )