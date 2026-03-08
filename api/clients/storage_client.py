"""
clients/storage_client.py

Uploads event payloads to Cloud Storage as immutable JSON blobs.

Purpose:
  Every webhook event that arrives gets its raw JSON payload saved to
  Cloud Storage before the agent pipeline runs. This gives you:

  1. An immutable audit record of exactly what came in — useful if you
     need to debug why an agent produced a certain output.

  2. A source of truth for re-runs — if you ever need to replay an event
     through a different model or updated prompt, the original payload
     is always available at a known path.

  3. A decoupled backup — even if Firestore data is accidentally deleted,
     the raw payloads are still in GCS.

Storage path convention:
  events/{eventId}/payload.json

  Example:
  gs://your-project-mindfeeder-events/events/abc-123-def/payload.json

  This path structure mirrors the Firestore collection hierarchy, which
  makes it easy to correlate a GCS blob with its Firestore document.

Why Cloud Storage and not just Firestore?
  Firestore document size limit is 1MB. Most order payloads are tiny,
  but WooCommerce orders can carry large metadata, product descriptions,
  and custom fields. Storing the raw payload in GCS removes any size
  concern entirely and keeps Firestore documents lean.

  GCS is also cheaper for storing rarely-accessed blobs than Firestore
  is for storing large document fields.

Connection:
  Uses Application Default Credentials (ADC) — same as the other clients.
  No credentials file needed locally or on Cloud Run.
"""

import logging
import os
import json
from typing import Optional

from google.cloud import storage

logger = logging.getLogger("mindfeeder.storage_client")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

GCS_BUCKET = os.environ.get("GCS_BUCKET", "mindfeeder-sim-bhavanakon-4214-mf-sim")

# ---------------------------------------------------------------------------
# Client — lazy initialised singleton
# ---------------------------------------------------------------------------
# One storage client shared across all requests.
# Creating a new client per request opens a new connection each time —
# wasteful and slower than reusing a single instance.

_client: Optional[storage.Client] = None


def _get_client() -> storage.Client:
    """
    Return the shared Cloud Storage client, creating it on first call.

    Uses ADC automatically — no explicit credentials needed.
    """
    global _client
    if _client is None:
        _client = storage.Client()
    return _client


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------

async def upload_event_payload(
    event_id: str,
    payload_json: str,
) -> str:
    """
    Upload the raw event payload JSON to Cloud Storage.

    Called by the webhook handler immediately after storing the event
    in Firestore — before the agent pipeline runs. This ensures the
    raw payload is always persisted even if agents fail.

    Parameters:
        event_id      The event UUID. Used to construct the blob path.
        payload_json  The raw JSON string exactly as received in the
                      webhook request body. Not re-serialised — the
                      original bytes are preserved.

    Returns:
        The gs:// URI of the uploaded blob.
        Example: "gs://my-project-events/events/abc-123/payload.json"
        This URI is stored in Firestore on the event document so you
        can find the blob directly from the event record.

    Raises:
        google.cloud.exceptions.GoogleCloudError  on upload failure.
        Unlike bq_client.py, this function DOES raise on failure.
        Rationale: if we cannot store the raw payload, we have no
        source of truth for re-runs. The webhook handler treats this
        as a non-blocking warning in practice (logs and continues),
        but the function itself surfaces the error to the caller.

    Note on sync vs async:
        storage.Client.upload_from_string() is synchronous — it blocks
        until the upload completes. For the current scale (one upload
        per webhook call, payloads typically under 10KB) this is fine.
        For high-throughput services, wrap in asyncio.to_thread():
            await asyncio.to_thread(blob.upload_from_string, ...)
    """

    blob_path = f"events/{event_id}/payload.json"

    logger.info(json.dumps({
        "message":    "Uploading payload to GCS",
        "event_id":   event_id,
        "bucket":     GCS_BUCKET,
        "blob_path":  blob_path,
        "bytes":      len(payload_json.encode("utf-8")),
    }))

    try:
        client = _get_client()
        bucket = client.bucket(GCS_BUCKET)
        blob   = bucket.blob(blob_path)

        # Set content type so the blob renders correctly in the GCS console
        # and can be fetched directly by HTTP clients
        blob.upload_from_string(
            payload_json,
            content_type="application/json",
        )

        gcs_uri = f"gs://{GCS_BUCKET}/{blob_path}"

        logger.info(json.dumps({
            "message":   "GCS upload complete",
            "event_id":  event_id,
            "gcs_uri":   gcs_uri,
            "bytes":     len(payload_json.encode("utf-8")),
        }))

        return gcs_uri

    except Exception as exc:
        logger.error(json.dumps({
            "message":   "GCS upload failed",
            "event_id":  event_id,
            "bucket":    GCS_BUCKET,
            "blob_path": blob_path,
            "error":     str(exc),
        }))
        # Re-raise so the caller knows the upload failed.
        # main.py logs a warning but continues — the pipeline still runs.
        raise


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

async def download_event_payload(event_id: str) -> Optional[str]:
    """
    Download the raw payload JSON for an event from Cloud Storage.

    Not used in the main pipeline — provided as a utility for:
      - Debugging: inspect exactly what payload triggered a run
      - Re-run tooling: fetch original payload to replay through agents
      - Admin tools: compare original input against agent outputs

    Parameters:
        event_id  The event UUID. Used to construct the blob path.

    Returns:
        The raw JSON string from the blob.
        None if the blob does not exist or the download fails.
    """
    blob_path = f"events/{event_id}/payload.json"

    try:
        client  = _get_client()
        bucket  = client.bucket(GCS_BUCKET)
        blob    = bucket.blob(blob_path)

        # Check existence before downloading to give a cleaner log message
        if not blob.exists():
            logger.warning(json.dumps({
                "message":   "GCS blob not found",
                "event_id":  event_id,
                "blob_path": blob_path,
            }))
            return None

        content = blob.download_as_text(encoding="utf-8")

        logger.info(json.dumps({
            "message":   "GCS download complete",
            "event_id":  event_id,
            "blob_path": blob_path,
            "bytes":     len(content.encode("utf-8")),
        }))

        return content

    except Exception as exc:
        logger.error(json.dumps({
            "message":   "GCS download failed",
            "event_id":  event_id,
            "blob_path": blob_path,
            "error":     str(exc),
        }))
        return None


# ---------------------------------------------------------------------------
# Generate signed URL (optional utility)
# ---------------------------------------------------------------------------

def get_signed_url(event_id: str, expiration_seconds: int = 3600) -> Optional[str]:
    """
    Generate a time-limited signed URL for direct browser access to a payload.

    Useful for admin tooling where you want to let an admin download
    the raw payload directly without proxying the bytes through your API.

    Parameters:
        event_id            The event UUID.
        expiration_seconds  How long the URL is valid. Default 1 hour.

    Returns:
        A signed HTTPS URL string valid for expiration_seconds.
        None if URL generation fails.

    Note:
        Signed URLs require the service account to have the
        roles/iam.serviceAccountTokenCreator role, or be generated
        from a service account key file. On Cloud Run with ADC this
        may require extra IAM setup. If you do not need direct
        browser download, skip this function entirely.
    """
    from datetime import timedelta

    blob_path = f"events/{event_id}/payload.json"

    try:
        client = _get_client()
        bucket = client.bucket(GCS_BUCKET)
        blob   = bucket.blob(blob_path)

        url = blob.generate_signed_url(
            expiration=timedelta(seconds=expiration_seconds),
            method="GET",
            version="v4",   # v4 signing is the current recommended version
        )

        logger.info(json.dumps({
            "message":    "Signed URL generated",
            "event_id":   event_id,
            "expires_in": expiration_seconds,
        }))

        return url

    except Exception as exc:
        logger.error(json.dumps({
            "message":   "Signed URL generation failed",
            "event_id":  event_id,
            "error":     str(exc),
        }))
        return None