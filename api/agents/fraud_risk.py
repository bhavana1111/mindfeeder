"""
agents/fraud_risk.py

FraudRiskAgent — evaluates an incoming WooCommerce order for fraud signals.

What it does:
  Sends the order details to Vertex AI with a structured prompt that asks
  the model to assess fraud risk. Returns a validated FraudRiskOutput object
  containing a risk level, confidence score, and list of reasons.

Output shape:
  {
    "risk_level": "low" | "medium" | "high",
    "confidence": 0.0 – 1.0,
    "reasons":    ["signal 1", "signal 2", ...]
  }

Fraud signals the prompt instructs the model to look for:
  - High order total ($100+ elevated, $500+ high)
  - Rush language in customer note ("ASAP", "leaving town", "urgent")
  - High quantity of expensive items (potential resale fraud)
  - Free email provider (gmail/yahoo/hotmail) combined with high total
  - Shipping address missing or incomplete
  - Single item ordered in unusually large quantity
"""

import json

from agents.base import call_vertex_structured
from schemas.agents import FraudRiskOutput, FRAUD_RISK_SCHEMA, UsageMetadata


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------
# Guidelines for writing good agent prompts:
#   1. Give the model a clear role ("You are a fraud risk analyst")
#   2. Inject the data using .format() — never hardcode order details
#   3. List the exact signals you want evaluated
#   4. Tell it to be conservative (prefer medium over extremes)
#   5. End with "Return ONLY valid JSON" — discourages extra commentary
# ---------------------------------------------------------------------------

PROMPT_TEMPLATE = """
You are a fraud risk analyst for an e-commerce platform.
Analyze the following WooCommerce order and return a structured fraud risk assessment.

Order details:
{order_json}

Evaluate these signals carefully:

1. ORDER TOTAL
   - Under $50:  generally low risk
   - $50-$200:   medium risk if other signals present
   - Over $200:  elevated — check other signals
   - Over $500:  high risk on its own

2. CUSTOMER NOTE URGENCY
   - Words like "ASAP", "urgent", "leaving town", "tomorrow", "right away"
     are social engineering indicators — customers trying to rush fulfillment
     to prevent fraud review. Flag these.

3. ITEM QUANTITY AND VALUE
   - Multiple units of the same high-value item suggests resale fraud.
   - qty > 2 on items over $50 each should be flagged.

4. EMAIL DOMAIN
   - Free providers (gmail, yahoo, hotmail, outlook) combined with
     a high order total ($150+) are a mild signal.
   - Temporary/disposable email domains are a strong signal.

5. SHIPPING ADDRESS
   - Missing zip code or country is suspicious.
   - Address completeness matters.

Scoring guidance:
  - "low":    No significant signals. Normal order.
  - "medium": 1-2 mild signals present. Worth a human glance.
  - "high":   Multiple strong signals. Hold for review.

Be conservative: when in doubt, prefer "medium" over "high".
Do not invent signals that are not present in the order data.

Return ONLY valid JSON matching the schema. No extra text or explanation.
"""


# ---------------------------------------------------------------------------
# Agent function
# ---------------------------------------------------------------------------

async def run_fraud_risk_agent(
    order: dict,
    event_id: str,
) -> tuple[FraudRiskOutput, UsageMetadata]:
    """
    Run the FraudRiskAgent for a single WooCommerce order.

    Parameters:
        order     The order dict from the webhook payload.
                  Expected keys: id, email, total, currency, items,
                  shipping_address, customer_note.
        event_id  The event UUID — passed through to base.py for log tracing.

    Returns:
        (FraudRiskOutput, UsageMetadata)
        FraudRiskOutput is already Pydantic-validated before being returned.
        UsageMetadata contains token counts + estimated cost for this agent call.

    Raises:
        ValueError   If Vertex AI returns JSON that fails Pydantic validation.
        Exception    For any Vertex API error (network, quota exceeded, etc).
    """

    # Build the prompt — inject the order as pretty-printed JSON
    # indent=2 makes it easier for the model to read nested structures
    prompt = PROMPT_TEMPLATE.format(
        order_json=json.dumps(order, indent=2)
    )

    # Delegate to the shared Vertex AI caller in base.py
    # base.py handles: API call, response parsing, Pydantic validation,
    # usage extraction, and cost calculation
    return await call_vertex_structured(
        prompt=prompt,
        response_schema=FRAUD_RISK_SCHEMA,
        output_model=FraudRiskOutput,
        agent_name="fraud_risk",
        event_id=event_id,
    )