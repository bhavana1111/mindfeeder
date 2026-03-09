import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Optional
from dotenv import load_dotenv
load_dotenv() 
import os



from fastapi import FastAPI, HTTPException, Request,Depends,Header
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


def log(level: str, message: str, **kwargs):
    entry = {
        "severity": level.upper(),
        "message": message,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **kwargs,
    }
    print(json.dumps(entry), flush=True)


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


class OrderItem(BaseModel):
    sku: str
    qty: int
    price: float


class ShippingAddress(BaseModel):
    country: str
    zip: str


class Order(BaseModel):
    id: int
    email: str
    total: float
    currency: str
    items: list[OrderItem]
    shipping_address: ShippingAddress
    customer_note: Optional[str] = None


class WebhookPayload(BaseModel):
    source: str
    event_type: str
    order: Order


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
                "outputJson": output,
                "model": result.usage.model,
                "createdAt": datetime.now(timezone.utc).isoformat(),
                "runType": "initial_run",
            },
        )

    await store_event(event_id, {"status": "complete"}, merge=True)

    await insert_usage_row(
        {
            "event_id": event_id,
            "uid": triggered_by,
            "model": result.usage.model,
            "prompt_tokens": result.usage.prompt_tokens,
            "output_tokens": result.usage.output_tokens,
            "total_tokens": result.usage.total_tokens,
            "prompt_chars": result.usage.prompt_chars,
            "output_chars": result.usage.output_chars,
            "estimated_cost_usd": result.usage.estimated_cost_usd,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    )

    await store_audit_log_entry(
        event_id,
        {
            "action": "initial_run",
            "triggeredBy": triggered_by,
            "triggeredAt": datetime.now(timezone.utc).isoformat(),
            "model": result.usage.model,
            "totalTokens": result.usage.total_tokens,
            "estimatedCostUsd": result.usage.estimated_cost_usd,
            "agentsRun": list(result.agent_outputs.keys()),
        },
    )

    log("INFO", "Pipeline complete", event_id=event_id)

    return {
        "outputs": result.agent_outputs,
        "usage": result.usage.model_dump(),
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
    Returns a list of all events ordered by createdAt descending.

    Response shape:
    {
        "events": [
            { "id", "status", "orderId", "orderEmail", "source", "eventType", "createdAt" },
            ...
        ],
        "count": 12
    }

    Query params:
        limit  – max events to return (default 50)
    """
    event_ids = await get_events(limit=limit)

    log("INFO", "Events listed", count=len(event_ids))

    return {
        "events": event_ids,
        "count": len(event_ids),
    }


@app.post("/webhook")
async def webhook(payload: WebhookPayload,request: Request):
    try:
        raw_body = await request.body()
        data = json.loads(raw_body)
        payload = WebhookPayload.model_validate(data)
    except Exception as exc:
        log("ERROR", "Payload parse failed", error=str(exc))
        raise HTTPException(status_code=422, detail=f"Invalid payload: {exc}")

    event_id = str(uuid.uuid4())

    log(
        "INFO",
        "Webhook received",
        event_id=event_id,
        source=payload.source,
        event_type=payload.event_type,
        order_id=payload.order.id,
        order_email=payload.order.email,
        order_total=payload.order.total,
    )

    await store_event(
        event_id,
        {
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "source": payload.source,
            "eventType": payload.event_type,
            "status": "processing",
            "payload": payload.model_dump(),
            "orderId": payload.order.id,
            "orderEmail": payload.order.email,
        },
    )

    try:
        gcs_path = await upload_event_payload(event_id, raw_body.decode("utf-8"))
        log("INFO", "Payload uploaded to GCS", event_id=event_id, gcs_path=gcs_path)
    except Exception as exc:
        log("WARNING", "GCS upload failed — continuing without blob", event_id=event_id, error=str(exc))
        gcs_path = None

    result = await _run_pipeline(
        payload=payload.model_dump(),
        event_id=event_id,
        triggered_by="webhook",
    )

    return {
        "event_id": event_id,
        "status": "complete",
        "gcs_path": gcs_path,
        **result,
    }


@app.get("/events/{event_id}")
async def get_event_detail(
    event_id: str
):
    """
    Returns the event document + all three agent outputs + audit log.

    Response shape:
    {
        "event":     { id, status, orderId, orderEmail, createdAt, ... },
        "outputs":   {
                       "fraud_risk":       { outputJson, model, runType },
                       "support_reply":    { outputJson, model, runType },
                       "fulfillment_note": { outputJson, model, runType }
                     },
        "audit_log": [ { action, triggeredBy, triggeredAt, totalTokens, estimatedCostUsd } ]
    }
    """
    # Fetch event document from Firestore
    event = await get_event(event_id)
    if not event:
        raise HTTPException(status_code=404, detail=f"Event {event_id} not found")

    # Fetch agent outputs subcollection
    outputs = await get_event_outputs(event_id)

    # Fetch audit log subcollection
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


    