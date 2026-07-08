"""SteamGuard AI subsystem (Phase 1: RAG-backed /ai/ask).

All AI calls are routed through the private `sg-litellm` Cloud Run service
which fronts NVIDIA NIM. No secrets are ever bundled with the desktop
loader — the loader hits the existing HMAC-authenticated `steamguard`
service, which in turn calls sg-litellm over private ingress.
"""
