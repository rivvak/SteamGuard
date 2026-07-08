"""Query-time retrieval over the Chroma collection."""

from __future__ import annotations

from typing import NamedTuple

from ai.client import get_llm
from ai.rag.chroma_gcs import COLLECTION_NAME, ensure_local_index, get_client

EMBED_MODEL = "sg-embed"


class Passage(NamedTuple):
    source: str
    text: str
    score: float


def _embed_query(text: str) -> list[float]:
    llm = get_llm()
    resp = llm.embeddings.create(
        model=EMBED_MODEL,
        input=text,
        extra_body={"input_type": "query"},
    )
    return resp.data[0].embedding


def retrieve(query: str, k: int = 4) -> list[Passage]:
    ensure_local_index()
    client = get_client()
    collection = client.get_or_create_collection(COLLECTION_NAME)
    q_emb = _embed_query(query)
    res = collection.query(query_embeddings=[q_emb], n_results=k)

    out: list[Passage] = []
    docs = res.get("documents", [[]])[0]
    metas = res.get("metadatas", [[]])[0]
    dists = res.get("distances", [[]])[0]
    for doc, meta, dist in zip(docs, metas, dists):
        out.append(
            Passage(
                source=meta.get("source", "unknown"),
                text=doc,
                score=1.0 - float(dist),  # cosine → similarity
            )
        )
    return out


def format_context(passages: list[Passage]) -> str:
    if not passages:
        return "(no matching documentation)"
    parts: list[str] = []
    for p in passages:
        parts.append(f"### {p.source}\n{p.text}")
    return "\n\n".join(parts)
