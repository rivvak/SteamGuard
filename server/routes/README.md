# server/routes/

Route modules that are mounted from `server/main.py` via
`app.include_router(...)`. Phase 1 introduces the AI subsystem here.

Register in `server/main.py` (near the other endpoint definitions):

```python
try:
    from server.routes.ai_ask import router as ai_ask_router
    app.include_router(ai_ask_router)
except Exception as exc:
    # AI subsystem is optional — never crash the license server if it fails to import
    logging.warning("AI subsystem not mounted: %s", exc)
```

The feature flag `AI_ASK_ENABLED=true` on the `steamguard` Cloud Run service
gates the endpoint at runtime — even when the router is mounted, the endpoint
returns 503 until the flag is set. This keeps rollout reversible.
