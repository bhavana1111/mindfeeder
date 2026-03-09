"""
clients/bq_client.py

Writes usage rows to the BigQuery usage_ledger table.

Purpose:
  Every pipeline run (initial + reruns) inserts one row into BigQuery.
  This gives you a queryable history of every Vertex AI call made —
  how many tokens were used, what it cost, and which event triggered it.

Why BigQuery for this and not Firestore?
  Firestore is great for per-document reads (fetch one event, fetch one user).
  BigQuery is great for analytical queries across many rows:
    - "How much did we spend on AI this week?"
    - "Which orders cost the most to process?"
    - "How many tokens did we use per day this month?"
  These questions are easy and cheap in BigQuery, painful in Firestore.

Insertion method: Streaming insert (insertAll)
  We use the BigQuery streaming insert API because:
    - Rows are available for queries within seconds of insertion
    - No batching or scheduling required — one row, one call
    - Simple API — just pass a list of dicts
  Trade-off: streaming inserts cost slightly more than batch loads,
  but for one row per webhook call the cost difference is negligible.

Idempotency:
  Each row is inserted with insert_id = event_id.
  BigQuery deduplicates rows with the same insert_id within ~1 minute.
  This means if the webhook handler retries and inserts the same row twice,
  BigQuery will silently discard the duplicate.
  Note: deduplication window is ~1 minute. After that, duplicates can appear.

Non-fatal design:
  BigQuery insert failures are logged but never raised.
  The webhook handler must not fail a customer order event because of
  a cost-tracking write. Observability is important but secondary to
  the core job of processing orders.

Table schema (must match infra/main.tf):
  event_id            STRING    REQUIRED
  uid                 STRING    NULLABLE
  model               STRING    NULLABLE
  prompt_tokens       INTEGER   NULLABLE
  output_tokens       INTEGER   NULLABLE
  total_tokens        INTEGER   NULLABLE
  prompt_chars        INTEGER   NULLABLE
  output_chars        INTEGER   NULLABLE
  estimated_cost_usd  FLOAT     NULLABLE
  created_at          TIMESTAMP REQUIRED
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from google.cloud import bigquery

logger = logging.getLogger("mindfeeder.bq_client")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BQ_PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "mindfeeder-sim-bhavanakon-4214")
BQ_DATASET = os.environ.get("BQ_DATASET", "mf_usage")
BQ_TABLE   = os.environ.get("BQ_TABLE", "usage_ledger")

# ---------------------------------------------------------------------------
# Client — lazy initialised singleton
# ---------------------------------------------------------------------------
# Same pattern as firestore_client.py — one client for the lifetime
# of the Cloud Run process, not one per request.

_client: Optional[bigquery.Client] = None


def _get_client() -> bigquery.Client:
    """
    Return the shared BigQuery client, creating it on first call.

    Uses ADC (Application Default Credentials) automatically.
    No credentials file needed locally or on Cloud Run.
    """
    global _client
    if _client is None:
        _client = bigquery.Client(project=BQ_PROJECT)
    return _client


def _table_ref() -> str:
    """
    Return the fully qualified BigQuery table reference.

    Format: project_id.dataset_id.table_id
    Example: my-project.mindfeeder.usage_ledger

    This string is what BigQuery uses to identify exactly which table
    to insert into. All three parts must be correct or the insert fails.
    """
    return f"{BQ_PROJECT}.{BQ_DATASET}.{BQ_TABLE}"


# ---------------------------------------------------------------------------
# Main insert function
# ---------------------------------------------------------------------------

async def insert_usage_row(row: dict):
    """
    Insert a single usage row into the BigQuery usage_ledger table.

    This function is intentionally non-fatal. If the insert fails for
    any reason (network issue, table not found, schema mismatch), it
    logs the error and returns normally. The caller (main.py) does not
    need to handle exceptions from this function.

    Parameters:
        row  Dict with these keys:
               event_id           str   — UUID of the event (also used as insert_id)
               uid                str   — user ID or "woocommerce" / "webhook_api"
               model              str   — Vertex AI model name
               prompt_tokens      int   — tokens in the prompt
               output_tokens      int   — tokens in the response
               total_tokens       int   — prompt + output tokens
               prompt_chars       int   — characters in the prompt
               output_chars       int   — characters in the response
               estimated_cost_usd float — calculated cost in USD
               created_at         str   — ISO 8601 timestamp

    Returns:
        None. Always returns normally — errors are logged, not raised.

    Example call from main.py:
        await insert_usage_row({
            "event_id":           event_id,
            "uid":                "woocommerce",
            "model":              "gemini-1.5-flash-001",
            "prompt_tokens":      312,
            "output_tokens":      89,
            "total_tokens":       401,
            "prompt_chars":       1240,
            "output_chars":       356,
            "estimated_cost_usd": 0.000289,
            "created_at":         "2025-01-15T10:30:00Z",
        })
    """

    # ── Ensure created_at is present ─────────────────────────────────────────
    # BigQuery TIMESTAMP fields require ISO 8601 format.
    # If the caller forgot to include created_at, set it here as a fallback.
    if "created_at" not in row or not row["created_at"]:
        row["created_at"] = datetime.now(timezone.utc).isoformat()

    # ── Coerce numeric types ──────────────────────────────────────────────────
    # Pydantic gives us the right types, but defensive coercion here prevents
    # BigQuery rejecting a row because a token count came in as a float.
    row = {
        **row,
        "prompt_tokens":      int(row.get("prompt_tokens",  0) or 0),
        "output_tokens":      int(row.get("output_tokens",  0) or 0),
        "total_tokens":       int(row.get("total_tokens",   0) or 0),
        "prompt_chars":       int(row.get("prompt_chars",   0) or 0),
        "output_chars":       int(row.get("output_chars",   0) or 0),
        "estimated_cost_usd": float(row.get("estimated_cost_usd", 0.0) or 0.0),
    }

    logger.info(json.dumps({
        "message":            "Inserting BQ usage row",
        "event_id":           row.get("event_id"),
        "model":              row.get("model"),
        "total_tokens":       row.get("total_tokens"),
        "estimated_cost_usd": row.get("estimated_cost_usd"),
    }))

    # ── Streaming insert ──────────────────────────────────────────────────────
    # insert_rows_json() is synchronous — it blocks until the insert
    # completes or fails. In a high-throughput service you would wrap
    # this in asyncio.to_thread() to avoid blocking the event loop.
    # For the current scale (one row per webhook) this is acceptable.
    #
    # row_ids: setting insert_id to event_id enables BQ deduplication.
    # If this row is inserted twice within ~1 minute (e.g. a retry),
    # BigQuery discards the duplicate silently.
    try:
        client = _get_client()
        errors = client.insert_rows_json(
            table=_table_ref(),
            json_rows=[row],
            row_ids=[row.get("event_id", "")],  # idempotency key
        )

        if errors:
            # insert_rows_json returns a list of error dicts, not an exception.
            # Each dict has "index" (row index) and "errors" (list of error info).
            logger.error(json.dumps({
                "message":  "BigQuery insert returned errors",
                "event_id": row.get("event_id"),
                "errors":   errors,
                "table":    _table_ref(),
            }))
            # Do NOT raise — non-fatal by design
        else:
            logger.info(json.dumps({
                "message":            "BigQuery usage row inserted",
                "event_id":           row.get("event_id"),
                "estimated_cost_usd": row.get("estimated_cost_usd"),
                "total_tokens":       row.get("total_tokens"),
            }))

    except Exception as exc:
        # Catch everything — network errors, auth errors, table not found, etc.
        # Log the full error but return normally so the webhook does not fail.
        logger.error(json.dumps({
            "message":  "BigQuery insert raised exception",
            "event_id": row.get("event_id"),
            "error":    str(exc),
            "table":    _table_ref(),
        }))
        # Do NOT re-raise


# ---------------------------------------------------------------------------
# Optional: query helpers (used if you add a spend widget to the UI)
# ---------------------------------------------------------------------------

async def get_event_usage(event_id: str) -> Optional[dict]:
    """
    Fetch the usage row for a specific event from BigQuery.

    Useful if you want to show cost details on the event detail page
    directly from BQ rather than from Firestore.

    Note on cost control:
        BigQuery charges per byte scanned. For small tables this is
        negligible, but always set maximum_bytes_billed as a safeguard
        to prevent runaway query costs.

    Parameters:
        event_id  The event UUID to look up.

    Returns:
        Dict of the usage row, or None if not found.
    """
    try:
        client = _get_client()

        # Parameterised query — never use string formatting for SQL
        # (prevents SQL injection even though BQ is less vulnerable than RDBMS)
        query = """
            SELECT
                event_id,
                uid,
                model,
                prompt_tokens,
                output_tokens,
                total_tokens,
                prompt_chars,
                output_chars,
                estimated_cost_usd,
                created_at
            FROM `{table}`
            WHERE event_id = @event_id
            ORDER BY created_at DESC
            LIMIT 1
        """.format(table=_table_ref())

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("event_id", "STRING", event_id)
            ],
            # Safety limit — prevents accidentally scanning huge tables
            # Adjust upward if table grows beyond this threshold
            maximum_bytes_billed=10 * 1024 * 1024,  # 10 MB max
        )

        query_job = client.query(query, job_config=job_config)
        results   = list(query_job.result())

        if results:
            row = dict(results[0])
            # Convert BigQuery datetime to ISO string for JSON serialisation
            if row.get("created_at"):
                row["created_at"] = row["created_at"].isoformat()
            return row

        return None

    except Exception as exc:
        logger.error(json.dumps({
            "message":  "BigQuery query failed",
            "event_id": event_id,
            "error":    str(exc),
        }))
        return None


async def get_daily_spend_summary() -> list[dict]:
    """
    Return total tokens and cost for the last 24 hours grouped by model.

    Used by the optional spend widget on the dashboard.

    Returns a list like:
        [
            { "model": "gemini-1.5-flash-001", "total_tokens": 12400,
              "total_cost_usd": 0.0031, "event_count": 15 },
        ]

    Returns empty list [] on any error.

    Note:
        maximum_bytes_billed is set to 50MB here since this query
        scans more rows than the single-event lookup above.
        Increase if your table grows and queries start hitting the limit.
    """
    try:
        client = _get_client()

        query = """
            SELECT
                model,
                COUNT(*)            AS event_count,
                SUM(total_tokens)   AS total_tokens,
                SUM(estimated_cost_usd) AS total_cost_usd
            FROM `{table}`
            WHERE created_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 24 HOUR)
            GROUP BY model
            ORDER BY total_cost_usd DESC
        """.format(table=_table_ref())

        job_config = bigquery.QueryJobConfig(
            maximum_bytes_billed=50 * 1024 * 1024,  # 50 MB max
        )

        query_job = client.query(query, job_config=job_config)
        results   = list(query_job.result())

        return [dict(row) for row in results]

    except Exception as exc:
        logger.error(json.dumps({
            "message": "BigQuery daily summary query failed",
            "error":   str(exc),
        }))
        return []