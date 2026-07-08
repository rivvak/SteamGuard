"""Thin OpenAI() factory pointed at the internal sg-litellm proxy.

Reads `LITELLM_URL` (Cloud Run private URL, e.g.
`https://sg-litellm-xxxxx-uc.a.run.app/v1`) and `LITELLM_MASTER_KEY`
from environment. Cloud Run mints an OIDC token for the internal call
via the metadata server — LiteLLM's master key is the API-level
authorization.
"""

from __future__ import annotations

import os
from functools import lru_cache

from openai import OpenAI


@lru_cache(maxsize=1)
def get_llm() -> OpenAI:
    base_url = os.environ["LITELLM_URL"].rstrip("/")
    if not base_url.endswith("/v1"):
        base_url = f"{base_url}/v1"
    return OpenAI(
        base_url=base_url,
        api_key=os.environ["LITELLM_MASTER_KEY"],
        timeout=60.0,
    )
