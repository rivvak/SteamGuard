"""One-shot ingest: read docs/**/*.md → chunk → embed via sg-embed → Chroma → GCS.

Run manually or in a Cloud Build step after each release:

    python -m ai.rag.ingest --docs-dir docs

Requires env vars:
- LITELLM_URL, LITELLM_MASTER_KEY  (for embeddings via sg-embed model)
- RAG_BUCKET                        (default: sg-rag-index)
- RAG_INDEX_VERSION                 (default: v1 — bump to v2, v3, ... for rollback)
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Iterable

from ai.client import get_llm
from ai.rag.chroma_gcs import (
    COLLECTION_NAME,
    LOCAL_DIR,
    get_client,
    reset_local,
    upload_index,
)

EMBED_MODEL = "sg-embed"
CHUNK_CHARS = 1200
CHUNK_OVERLAP = 200


def _split(text: str) -> list[str]:
    """Cheap paragraph-first chunker with hard char cap + overlap."""
    text = re.sub(r"\r\n?", "\n", text).strip()
    if not text:
        return []
    paras = re.split(r"\n\s*\n", text)
    chunks: list[str] = []
    buf = ""
    for p in paras:
        candidate = f"{buf}\n\n{p}".strip() if buf else p
        if len(candidate) <= CHUNK_CHARS:
            buf = candidate
            continue
        if buf:
            chunks.append(buf)
        while len(p) > CHUNK_CHARS:
            chunks.append(p[:CHUNK_CHARS])
            p = p[CHUNK_CHARS - CHUNK_OVERLAP:]
        buf = p
    if buf:
        chunks.append(buf)
    return chunks


def _iter_docs(docs_dir: Path) -> Iterable[tuple[str, str]]:
    for md in sorted(docs_dir.rglob("*.md")):
        yield md.relative_to(docs_dir).as_posix(), md.read_text(encoding="utf-8", errors="ignore")


def _embed_batch(texts: list[str]) -> list[list[float]]:
    llm = get_llm()
    resp = llm.embeddings.create(
        model=EMBED_MODEL,
        input=texts,
        extra_body={"input_type": "passage"},
    )
    return [d.embedding for d in resp.data]


def ingest(docs_dir: Path) -> int:
    reset_local()
    client = get_client()
    collection = client.get_or_create_collection(COLLECTION_NAME)

    ids: list[str] = []
    docs: list[str] = []
    metas: list[dict] = []

    for rel, body in _iter_docs(docs_dir):
        for i, chunk in enumerate(_split(body)):
            ids.append(f"{rel}#{i}")
            docs.append(chunk)
            metas.append({"source": rel, "chunk": i})

    if not docs:
        raise SystemExit("no markdown found under docs/")

    # embed in batches of 32 to keep NIM request sizes sane
    embeddings: list[list[float]] = []
    for i in range(0, len(docs), 32):
        embeddings.extend(_embed_batch(docs[i : i + 32]))

    collection.add(ids=ids, documents=docs, embeddings=embeddings, metadatas=metas)
    upload_index()
    return len(docs)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--docs-dir", default="docs")
    args = ap.parse_args()
    n = ingest(Path(args.docs_dir))
    print(f"ingested {n} chunks into {COLLECTION_NAME} @ {LOCAL_DIR} + gcs")


if __name__ == "__main__":
    main()
