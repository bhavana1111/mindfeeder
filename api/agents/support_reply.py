"""
agents/support_reply.py

SupportReplyAgent — drafts a customer-facing support email for a WooCommerce order.

What it does:
  Sends the order details to Vertex AI with a structured prompt that asks
  the model to write a professional support email. Returns a validated
  SupportReplyOutput object containing a subject line, email body, tone
  classification, and list of disclaimers.

Output shape:
  {
    "subject":     "Your order #12345 is confirmed",
    "body":        "Hi there, thank you for your order...",
    "tone":        "friendly" | "professional" | "urgent",
    "disclaimers": ["Orders cannot be modified after...", ...]
  }

Tone selection logic (prompt-guided):
  - "friendly"      Customer note is casual or has urgency — warm and proactive
  - "professional"  Standard order, no special note — clean and business-like
  - "urgent"        High-value order or explicit urgency signal — reassuring and fast

Disclaimer selection logic (prompt-guided):
  - Shipping timeframe disclaimer always included
  - Order modification cutoff included if customer note requests changes
  - Return policy included for high-value orders
  - Empty list [] is valid if no disclaimers are applicable
"""

import json

from agents.base import call_vertex_structured
from schemas.agents import SupportReplyOutput, SUPPORT_REPLY_SCHEMA, UsageMetadata


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------
# This prompt is longer than the fraud prompt because email writing requires
# more context about style, length, and what to include/avoid.
#
# Key prompt engineering decisions here:
#   1. "Address the customer by first name if available" — personalisation
#   2. "Under 150 words" — prevents the model writing an essay
#   3. Explicit tone rules tied to observable order signals
#   4. "Do not invent information" — guards against hallucinated tracking numbers
#   5. Disclaimer rules are specific so output is consistent across orders
# ---------------------------------------------------------------------------

PROMPT_TEMPLATE = """
You are a customer support specialist for a friendly e-commerce store.
Draft a professional support email for the following WooCommerce order.

Order details:
{order_json}

Email writing guidelines:

1. GREETING
   - Use "Hi there" if you cannot determine the customer's first name from the email.
   - If the email looks like a real name (e.g. jordan@...), use "Hi Jordan".
   - Never use "Dear Sir/Madam" — too formal.

2. BODY CONTENT
   - Always confirm the order was received and is being processed.
   - If the customer_note mentions urgency (ASAP, tomorrow, leaving town):
     acknowledge it directly and reassure them you are prioritising it.
   - If there is no customer_note, keep the body generic and professional.
   - Do NOT invent information like tracking numbers, delivery dates, or
     product details that are not in the order data.
   - Keep the body under 150 words. Customers do not read long emails.

3. TONE SELECTION
   - "friendly":      customer note is present and casual/urgent. Warm, empathetic.
   - "professional":  no customer note, or note is neutral. Clean, business-like.
   - "urgent":        order total is over $300 OR customer note expresses strong urgency.
                      Reassuring and action-oriented.

4. SIGN-OFF
   - End with: "Best regards, The Support Team"
   - Do not include a specific person's name.

5. DISCLAIMERS
   Include relevant disclaimers from this list (use exact wording):
   - "Processing times are 1-2 business days. Shipping times vary by carrier."
   - "Orders cannot be modified or cancelled after they enter processing."
   - "For returns and refunds, please contact us within 30 days of delivery."
   - "High-value orders may require additional verification before shipping."

   Rules for which disclaimers to include:
   - Always include the processing time disclaimer.
   - Include the modification disclaimer if customer_note asks for changes.
   - Include the returns disclaimer if order total is over $100.
   - Include the verification disclaimer if order total is over $300.
   - Disclaimers list can be empty [] if none of the above apply.

Return ONLY valid JSON matching the schema. No extra text, no markdown formatting.
"""


# ---------------------------------------------------------------------------
# Agent function
# ---------------------------------------------------------------------------

async def run_support_reply_agent(
    order: dict,
    event_id: str,
) -> tuple[SupportReplyOutput, UsageMetadata]:
    """
    Run the SupportReplyAgent for a single WooCommerce order.

    Parameters:
        order     The order dict from the webhook payload.
                  Expected keys: id, email, total, currency, items,
                  shipping_address, customer_note.
        event_id  The event UUID — passed through to base.py for log tracing.

    Returns:
        (SupportReplyOutput, UsageMetadata)
        SupportReplyOutput is already Pydantic-validated before being returned.
        UsageMetadata contains token counts + estimated cost for this agent call.

    Raises:
        ValueError   If Vertex AI returns JSON that fails Pydantic validation.
        Exception    For any Vertex API error (network, quota exceeded, etc).
    """

    # Inject the order data into the prompt as readable JSON
    prompt = PROMPT_TEMPLATE.format(
        order_json=json.dumps(order, indent=2)
    )

    return await call_vertex_structured(
        prompt=prompt,
        response_schema=SUPPORT_REPLY_SCHEMA,
        output_model=SupportReplyOutput,
        agent_name="support_reply",
        event_id=event_id,
    )