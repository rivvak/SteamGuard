FROM ghcr.io/berriai/litellm:main-stable

# LiteLLM's official image already includes the CLI + all providers.
# We just bake in our routing config; secrets are injected at runtime
# via Cloud Run env-var-from-secret bindings.

COPY config.yaml /app/config.yaml

# Cloud Run injects PORT; LiteLLM binds via --port flag
ENV LITELLM_MODE=PRODUCTION
CMD ["sh", "-c", "litellm --config /app/config.yaml --host 0.0.0.0 --port ${PORT:-4000}"]
