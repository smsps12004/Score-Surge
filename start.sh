#!/bin/sh
# Starts Score Surge inside the Docker image (see Dockerfile). Added 24 Oct 2026.
set -e

# Render mounts Secret Files at /etc/secrets/<name>. Streamlit reads
# .streamlit/secrets.toml, so copy it there. Never commit secrets.toml.
if [ -f /etc/secrets/secrets.toml ]; then
    mkdir -p .streamlit
    cp /etc/secrets/secrets.toml .streamlit/secrets.toml
elif [ ! -f .streamlit/secrets.toml ]; then
    echo "WARNING: no secrets.toml found (expected /etc/secrets/secrets.toml)." >&2
fi

# Render sets PORT (10000 by default).
exec streamlit run app.py \
    --server.port "${PORT:-10000}" \
    --server.address 0.0.0.0 \
    --server.headless true \
    --browser.gatherUsageStats false
