"""GCS-backed persistent Chroma client.

We sync a Chroma persist directory to/from `gs://sg-rag-index/` on cold start
and after each ingest. Chroma is embedded in-process — no separate vector-DB
service is provisioned.

Design notes:
- Cloud Run instances can be pre-warmed by running `ensure_local_index()`
  during FastAPI startup.
- The bucket holds one directory per index version (`v1/`, `v2/`, …) so a bad
  ingest can be rolled back by flipping `RAG_INDEX_VERSION` in Cloud Run.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import chromadb
from chromadb.config import Settings
from google.cloud import storage

BUCKET_NAME = os.environ.get("RAG_BUCKET", "sg-rag-index")
INDEX_VERSION = os.environ.get("RAG_INDEX_VERSION", "v1")
LOCAL_DIR = Path(os.environ.get("RAG_LOCAL_DIR", "/tmp/sg-rag-index"))
COLLECTION_NAME = "steamguard_docs"


def _bucket() -> storage.Bucket:
    return storage.Client().bucket(BUCKET_NAME)


def download_index() -> None:
    """Pull the current index version from GCS into LOCAL_DIR."""
    LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    bucket = _bucket()
    prefix = f"{INDEX_VERSION}/"
    for blob in bucket.list_blobs(prefix=prefix):
        rel = blob.name[len(prefix):]
        if not rel:
            continue
        target = LOCAL_DIR / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        blob.download_to_filename(str(target))


def upload_index() -> None:
    """Push LOCAL_DIR up to gs://.../<INDEX_VERSION>/."""
    bucket = _bucket()
    for path in LOCAL_DIR.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(LOCAL_DIR).as_posix()
        blob = bucket.blob(f"{INDEX_VERSION}/{rel}")
        blob.upload_from_filename(str(path))


def get_client() -> chromadb.PersistentClient:
    LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(
        path=str(LOCAL_DIR),
        settings=Settings(anonymized_telemetry=False),
    )


def ensure_local_index() -> None:
    """Called on Cloud Run cold start. Idempotent."""
    if any(LOCAL_DIR.rglob("*")):
        return
    download_index()


def reset_local() -> None:
    if LOCAL_DIR.exists():
        shutil.rmtree(LOCAL_DIR)
    LOCAL_DIR.mkdir(parents=True, exist_ok=True)
