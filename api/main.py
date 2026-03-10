import json
import logging
import os
import uuid
import hmac
import hashlib
import base64
from datetime import datetime, timezone
from typing import Optional
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException, Request, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from agents.orchestrator import run_orchestrator
from clients.bq_client import insert_usage_row
from clients.firestore_client import (
    get_audit_log,
    store_audit_log_entry,
    store_event,
    get_event,
    get_events,
    get_event_outputs,
    store_event_output,
)
from clients.storage_client import upload_event_payload
from schemas.agents import OrchestratorResult

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mindfeeder")

WEBHOOK_SECRET = os.environ.get("WC_WEBHOOK_SECRET", "")


def log(level: str, message: str, **kwargs):
    entry = {
        "severity": level.upper(),
        "message": message,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **kwargs,
    }
    print(json.dumps(entry), flush=True)


def verify_woocommerce_signature(payload: bytes, signature: str) -> bool:
    """
    WooCommerce signs the raw request body using HMAC-SHA256
    and base64-encodes the result into X-WC-Webhook-Signature header.
    hmac.compare_digest() prevents timing attacks.
    """
    if not WEBHOOK_SECRET:
        log("WARNING", "WC_WEBHOOK_SECRET not set — skipping HMAC verification")
        return True

    expected = base64.b64encode(
        hmac.new(
            WEBHOOK_SECRET.encode("utf-8"),
            payload,
            hashlib.sha256,
        ).digest()
    ).decode("utf-8")

    return hmac.compare_digest(expected, signature)


app = FastAPI(
    title="Mindfeeder AgentOps API",
    version="1.1.0",
    description="WooCommerce order triage via Vertex AI agent pipeline",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── WooCommerce native payload models ─────────────────────────────────────────
# WooCommerce sends the raw order object directly — no source/event_type wrapper.
# These models match the real WooCommerce REST API v3 order shape.

class WCBillingAddress(BaseModel):
    first_name: str = ""
    last_name:  str = ""
    email:      str = ""
    phone:      str = ""
    address_1:  str = ""
    city:       str = ""
    state:      str = ""
    postcode:   str = ""
    country:    str = ""

class WCShippingAddress(BaseModel):
    first_name: str = ""
    last_name:  str = ""
    address_1:  str = ""
    city:       str = ""
    state:      str = ""
    postcode:   str = ""
    country:    str = ""

class WCLineItem(BaseModel):
    id:         int = 0
    name:       str = ""
    product_id: int = 0
    quantity:   int = 1
    subtotal:   str = "0.00"
    total:      str = "0.00"
    sku:        str = ""
    price:      float = 0.0

class WCWebhookPayload(BaseModel):
    id:                   int
    status:               str
    currency:             str = "USD"
    total:                str = "0.00"
    customer_id:          int = 0
    customer_note:        str = ""
    payment_method:       str = ""
    payment_method_title: str = ""
    billing:              WCBillingAddress  = WCBillingAddress()
    shipping:             WCShippingAddress = WCShippingAddress()
    line_items:           list[WCLineItem]  = []
    date_created:         str = ""
# ─────────────────────────────────────────────────────────────────────────────


async def _run_pipeline(
    payload: dict,
    event_id: str,
    triggered_by: str,
) -> dict:
    try:
        result: OrchestratorResult = await run_orchestrator(payload, event_id)

        log(
            "INFO",
            "Orchestrator complete",
            event_id=event_id,
            agents_run=list(result.agent_outputs.keys()),
            total_tokens=result.usage.total_tokens,
            estimated_cost_usd=result.usage.estimated_cost_usd,
        )

    except Exception as exc:
        log("ERROR", "Orchestrator failed", event_id=event_id, error=str(exc))
        await store_event(event_id, {"status": "failed", "error": str(exc)}, merge=True)
        raise HTTPException(status_code=500, detail=f"Agent pipeline failed: {exc}")

    for agent_name, output in result.agent_outputs.items():
        await store_event_output(
            event_id,
            agent_name,
            {
                "outputJson":  output,
                "model":       result.usage.model,
                "createdAt":   datetime.now(timezone.utc).isoformat(),
                "runType":     "initial_run",
            },
        )

    await store_event(event_id, {"status": "complete"}, merge=True)

    await insert_usage_row(
        {
            "event_id":           event_id,
            "uid":                triggered_by,
            "model":              result.usage.model,
            "prompt_tokens":      result.usage.prompt_tokens,
            "output_tokens":      result.usage.output_tokens,
            "total_tokens":       result.usage.total_tokens,
            "prompt_chars":       result.usage.prompt_chars,
            "output_chars":       result.usage.output_chars,
            "estimated_cost_usd": result.usage.estimated_cost_usd,
            "created_at":         datetime.now(timezone.utc).isoformat(),
        }
    )

    await store_audit_log_entry(
        event_id,
        {
            "action":           "initial_run",
            "triggeredBy":      triggered_by,
            "triggeredAt":      datetime.now(timezone.utc).isoformat(),
            "model":            result.usage.model,
            "totalTokens":      result.usage.total_tokens,
            "estimatedCostUsd": result.usage.estimated_cost_usd,
            "agentsRun":        list(result.agent_outputs.keys()),
        },
    )

    log("INFO", "Pipeline complete", event_id=event_id)

    return {
        "outputs": result.agent_outputs,
        "usage":   result.usage.model_dump(),
    }


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "ts": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/events")
async def list_events(limit: int = 50):
    """
    Returns a list of all event IDs ordered by createdAt descending.
    """
    event_ids = await get_events(limit=limit)
    log("INFO", "Events listed", count=len(event_ids))
    return {
        "events": event_ids,
        "count":  len(event_ids),
    }


@app.post("/webhook")
async def webhook(request: Request):
    """
    Receives WooCommerce order webhooks.

    Security:
        Verifies X-WC-Webhook-Signature header using HMAC-SHA256.
        Requests with invalid or missing signatures are rejected with 401.
        The secret must match what is configured in WooCommerce:
        WooCommerce → Settings → Advanced → Webhooks → Secret

    WooCommerce ping:
        When you save a webhook in WooCommerce it sends "webhook_id=1"
        to confirm the URL is reachable. We return 200 immediately.

    Payload normalization:
        WooCommerce sends the raw order object directly.
        We normalize it into our internal format before running the pipeline.
    """
    raw_body = await request.body()

    # ── WooCommerce ping ──────────────────────────────────────────────────────
    if b"webhook_id" in raw_body:
        log("INFO", "WooCommerce ping received — URL verified")
        return {"status": "ok", "message": "ping received"}
    # ─────────────────────────────────────────────────────────────────────────

    # ── HMAC verification ─────────────────────────────────────────────────────
    signature = request.headers.get("X-WC-Webhook-Signature", "")
    if not verify_woocommerce_signature(raw_body, signature):
        log("WARNING", "Webhook rejected — invalid HMAC signature")
        raise HTTPException(status_code=401, detail="Invalid webhook signature")
    # ─────────────────────────────────────────────────────────────────────────

    try:
        data = json.loads(raw_body)
        wc = WCWebhookPayload.model_validate(data)
    except Exception as exc:
        log("ERROR", "Payload parse failed", error=str(exc))
        raise HTTPException(status_code=422, detail=f"Invalid payload: {exc}")

    # ── Normalize into internal format ────────────────────────────────────────
    normalized_payload = {
        "source":     "woocommerce",
        "event_type": "order.created",
        "order": {
            "id":       wc.id,
            "email":    wc.billing.email,
            "total":    float(wc.total),
            "currency": wc.currency,
            "items": [
                {
                    "sku":   item.sku or item.name,
                    "qty":   item.quantity,
                    "price": item.price,
                }
                for item in wc.line_items
            ],
            "shipping_address": {
                "country": wc.shipping.country,
                "zip":     wc.shipping.postcode,
                "city":    wc.shipping.city,
                "state":   wc.shipping.state,
            },
            "billing_address": {
                "country": wc.billing.country,
                "zip":     wc.billing.postcode,
                "city":    wc.billing.city,
                "state":   wc.billing.state,
            },
            "customer_name":  f"{wc.billing.first_name} {wc.billing.last_name}".strip(),
            "customer_note":  wc.customer_note,
            "payment_method": wc.payment_method_title,
            "status":         wc.status,
        }
    }
    # ─────────────────────────────────────────────────────────────────────────

    event_id = str(uuid.uuid4())

    log(
        "INFO",
        "Webhook received",
        event_id=event_id,
        order_id=wc.id,
        order_email=wc.billing.email,
        order_total=wc.total,
        status=wc.status,
        items_count=len(wc.line_items),
    )

    await store_event(
        event_id,
        {
            "createdAt":  datetime.now(timezone.utc).isoformat(),
            "source":     "woocommerce",
            "eventType":  "order.created",
            "status":     "processing",
            "payload":    normalized_payload,
            "orderId":    wc.id,
            "orderEmail": wc.billing.email,
        },
    )

    try:
        gcs_path = await upload_event_payload(event_id, raw_body.decode("utf-8"))
        log("INFO", "Payload uploaded to GCS", event_id=event_id, gcs_path=gcs_path)
    except Exception as exc:
        log("WARNING", "GCS upload failed — continuing", event_id=event_id, error=str(exc))
        gcs_path = None

    result = await _run_pipeline(
        payload=normalized_payload,
        event_id=event_id,
        triggered_by="webhook",
    )

    return {
        "event_id": event_id,
        "status":   "complete",
        "gcs_path": gcs_path,
        **result,
    }


@app.get("/events/{event_id}")
async def get_event_detail(event_id: str):
    """
    Returns the event document + all agent outputs + audit log.
    """
    event = await get_event(event_id)
    if not event:
        raise HTTPException(status_code=404, detail=f"Event {event_id} not found")

    outputs   = await get_event_outputs(event_id)
    audit_log = await get_audit_log(event_id)

    log("INFO", "Event fetched",
        event_id=event_id,
        status=event.get("status"),
        outputs_count=len(outputs),
        audit_log_count=len(audit_log))

    return {
        "event":     event,
        "outputs":   outputs,
        "audit_log": audit_log,
    }


    