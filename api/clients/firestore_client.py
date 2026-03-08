"""
clients/firestore_client.py

Thin async wrappers around the Firestore Admin SDK.

Why async?
  FastAPI is an async framework. If you use synchronous Firestore calls
  inside an async route handler, they block the event loop — meaning no
  other requests can be handled while Firestore is waiting to respond.
  AsyncClient solves this by releasing the event loop while waiting,
  so other requests can run concurrently.

Collections and their purpose:
  users/{uid}
      User profile created on first Google login.
      Fields: email, role ("user" | "admin"), createdAt
      Role is set to "user" on creation and only changed via /admin/users/{uid}/role.

  events/{eventId}
      One document per webhook event received.
      Fields: createdAt, source, eventType, status, payload, orderId, orderEmail
      status transitions: "processing" → "complete" | "failed"

  events/{eventId}/outputs/{agentName}
      Subcollection — one document per agent per event.
      Agent names: "fraud_risk", "support_reply", "fulfillment_note"
      Fields: outputJson, model, createdAt, runType ("initial_run" | "rerun")
      Re-runs overwrite these documents — latest result always at the same path.

  events/{eventId}/audit_log/{autoId}
      Subcollection — one document per pipeline execution (initial + reruns).
      Documents are never overwritten — append only.
      Fields: action, triggeredBy, triggeredAt, model, totalTokens,
              estimatedCostUsd, agentsRun[]

Connection:
  Uses Application Default Credentials (ADC).
  Locally: set up with "gcloud auth application-default login"
  On Cloud Run: uses the service account attached to the Cloud Run service.
  No credentials file needed in either case.
"""

import os
import logging
from datetime import datetime, timezone
from typing import Optional

from google.cloud.firestore_v1.async_client import AsyncClient

logger = logging.getLogger("mindfeeder.firestore")

# ---------------------------------------------------------------------------
# Client — lazy initialised singleton
# ---------------------------------------------------------------------------
# We create one AsyncClient and reuse it across all requests.
# Creating a new client per request would be wasteful — each client
# opens its own connection pool to Firestore.

_db: Optional[AsyncClient] = None


def _get_db() -> AsyncClient:
    """
    Return the shared Firestore AsyncClient, creating it on first call.

    Uses ADC automatically — no explicit credentials needed.
    GOOGLE_CLOUD_PROJECT env var tells the client which project to use.
    """
    global _db
    if _db is None:
        project = os.environ.get("GOOGLE_CLOUD_PROJECT")
        _db = AsyncClient(project=project)
    return _db


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

async def store_event(event_id: str, data: dict, merge: bool = False):
    """
    Create or update an event document at events/{event_id}.

    Parameters:
        event_id  The UUID assigned to this event by the webhook handler.
        data      Dict of fields to write.
        merge     If True, only the provided fields are updated — existing
                  fields not in data are left untouched.
                  If False (default), the entire document is replaced.

    Usage:
        # Initial creation — replace entire document
        await store_event(event_id, { "status": "processing", ... })

        # Status update — merge so other fields are not wiped
        await store_event(event_id, { "status": "complete" }, merge=True)
    """
    db  = _get_db()
    ref = db.collection("events").document(event_id)

    if merge:
        await ref.set(data, merge=True)
    else:
        await ref.set(data)

    logger.debug({
        "message":  "Event stored",
        "event_id": event_id,
        "merge":    merge,
        "fields":   list(data.keys()),
    })


async def get_event(event_id: str) -> Optional[dict]:
    """
    Fetch a single event document by ID.

    Returns:
        Dict with all event fields plus "id" key set to the document ID.
        None if the document does not exist.
    """
    db  = _get_db()
    doc = await db.collection("events").document(event_id).get()

    if doc.exists:
        return {"id": doc.id, **doc.to_dict()}

    return None


async def list_events(limit: int = 50) -> list[dict]:
    """
    Return the most recent events ordered by createdAt descending.

    Parameters:
        limit  Maximum number of events to return. Default 50.
                Keep this reasonable — Firestore charges per document read.

    Returns:
        List of event dicts, each with "id" field added.
        Empty list if no events exist yet.
    """
    db = _get_db()

    query = (
        db.collection("events")
        .order_by("createdAt", direction="DESCENDING")
        .limit(limit)
    )

    results = []
    async for doc in query.stream():
        results.append({"id": doc.id, **doc.to_dict()})

    return results


# ---------------------------------------------------------------------------
# Agent outputs (subcollection)
# ---------------------------------------------------------------------------

async def store_event_output(event_id: str, agent_name: str, data: dict):
    """
    Store a single agent's output under events/{eventId}/outputs/{agentName}.

    The document ID is the agent name — so there is exactly one document
    per agent per event. Re-runs overwrite the previous output so the
    subcollection always reflects the most recent run.

    Parameters:
        event_id    The parent event UUID.
        agent_name  One of: "fraud_risk", "support_reply", "fulfillment_note"
        data        Dict containing: outputJson, model, createdAt, runType

    Note:
        The audit log (separate subcollection) preserves the history of
        every run. This outputs subcollection only keeps the latest result.
    """
    db  = _get_db()
    ref = (
        db.collection("events")
        .document(event_id)
        .collection("outputs")
        .document(agent_name)
    )
    await ref.set(data)

    logger.debug({
        "message":    "Agent output stored",
        "event_id":   event_id,
        "agent_name": agent_name,
        "run_type":   data.get("runType"),
    })


async def get_event_outputs(event_id: str) -> dict:
    """
    Return all agent outputs for an event as a dict keyed by agent name.

    Returns:
        Dict like:
        {
            "fraud_risk":       { outputJson: {...}, model: "...", ... },
            "support_reply":    { outputJson: {...}, model: "...", ... },
            "fulfillment_note": { outputJson: {...}, model: "...", ... },
        }
        Empty dict {} if no outputs exist yet (event still processing).
    """
    db  = _get_db()
    ref = (
        db.collection("events")
        .document(event_id)
        .collection("outputs")
    )

    results = {}
    async for doc in ref.stream():
        results[doc.id] = doc.to_dict()

    return results


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

async def upsert_user(uid: str, email: str):
    """
    Create a user document if it does not exist.
    If it does exist, update the email but never change the role.

    This is called on every Google login via /users/sync.

    Why never downgrade the role?
        If an admin signs in and this function ran set({role: "user"}),
        they would lose their admin access on every login. By only writing
        role on creation and leaving it alone afterwards, role changes
        made via /admin/users/{uid}/role are preserved across logins.

    Parameters:
        uid    Google account ID (providerAccountId from NextAuth).
        email  User's email address from Google.
    """
    db  = _get_db()
    ref = db.collection("users").document(uid)
    doc = await ref.get()

    if not doc.exists:
        # First login — create document with default role
        await ref.set({
            "email":     email,
            "role":      "user",    # default — promote to admin manually
            "createdAt": datetime.now(timezone.utc).isoformat(),
        })
        logger.info({
            "message": "New user created",
            "uid":     uid,
            "email":   email,
        })
    else:
        # Returning user — update email only, never touch role
        await ref.set({"email": email}, merge=True)


async def get_user(uid: str) -> Optional[dict]:
    """
    Fetch a single user document by uid.

    Returns:
        Dict with user fields plus "uid" key set to the document ID.
        None if the user does not exist.
    """
    db  = _get_db()
    doc = await db.collection("users").document(uid).get()

    if doc.exists:
        return {"uid": doc.id, **doc.to_dict()}

    return None


async def list_users() -> list[dict]:
    """
    Return all users in the users collection.

    Used by the /admin/users endpoint to populate the admin page.
    No pagination — acceptable for small user counts (< 1000).
    Add cursor-based pagination if the user base grows.

    Returns:
        List of user dicts, each with "uid" field added.
    """
    db  = _get_db()
    results = []

    async for doc in db.collection("users").stream():
        results.append({"uid": doc.id, **doc.to_dict()})

    return results


async def update_user_role(uid: str, role: str):
    """
    Update a user's role. Called by PATCH /admin/users/{uid}/role.

    Parameters:
        uid   The user's Google account ID.
        role  Must be "admin" or "user" — validated in main.py before
              this function is called.

    Uses merge=True so only the role field is updated.
    All other user fields (email, createdAt) are left untouched.
    """
    db  = _get_db()
    await db.collection("users").document(uid).set(
        {"role": role},
        merge=True,
    )

    logger.info({
        "message":  "User role updated",
        "uid":      uid,
        "new_role": role,
    })


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

async def store_audit_log_entry(event_id: str, entry: dict):
    """
    Append an immutable audit log entry for a pipeline execution.

    Collection path: events/{eventId}/audit_log/{autoId}

    Firestore auto-generates the document ID, which means:
      - Entries are never overwritten
      - Every run (initial + reruns) creates a new document
      - The collection is an append-only log by design

    Each entry records:
      action           "initial_run" or "rerun"
      triggeredBy      uid of the user who triggered it, or "woocommerce"
      triggeredAt      ISO 8601 timestamp
      model            Vertex AI model used
      totalTokens      Combined tokens across all 3 agents for this run
      estimatedCostUsd Combined cost for this run
      agentsRun        List of agent names that ran

    Parameters:
        event_id  The parent event UUID.
        entry     Dict of audit log fields (see above).
    """
    db      = _get_db()
    col_ref = (
        db.collection("events")
        .document(event_id)
        .collection("audit_log")
    )

    # .add() lets Firestore auto-generate the document ID
    # This is what makes the log append-only
    await col_ref.add(entry)

    logger.info({
        "message":   "Audit log entry written",
        "event_id":  event_id,
        "action":    entry.get("action"),
        "triggered_by": entry.get("triggeredBy"),
    })


async def get_audit_log(event_id: str) -> list[dict]:
    """
    Return all audit log entries for an event, ordered oldest first.

    Used by GET /events/{id} to return the full run history to the UI.
    The event detail page renders these as a timeline.

    Returns:
        List of audit log entry dicts, each with "id" field added.
        Ordered by triggeredAt ascending (oldest run first).
        Empty list if no entries exist (should not happen in normal operation).
    """
    db  = _get_db()
    ref = (
        db.collection("events")
        .document(event_id)
        .collection("audit_log")
        .order_by("triggeredAt")
    )

    results = []
    async for doc in ref.stream():
        results.append({"id": doc.id, **doc.to_dict()})

    return results