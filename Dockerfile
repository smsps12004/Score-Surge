# Score Surge — always-on hosting (Render, Docker runtime). Added 24 Oct 2026.
#
# Why Docker: the profile-sheet reader needs the Tesseract OCR program (packages.txt
# on Streamlit Cloud). Render's plain Python runtime can't apt-install it; Docker can.
# Streamlit Cloud ignores this file, so score-surge.streamlit.app keeps working as-is.
#
# Secrets: never baked into the image. On Render, add a Secret File named
# `secrets.toml` with the same contents as Streamlit Cloud's Secrets box; start.sh
# copies it from /etc/secrets/ to .streamlit/secrets.toml at startup.

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Same system packages as packages.txt (Streamlit Cloud's list).
COPY packages.txt /tmp/packages.txt
RUN apt-get update \
    && xargs -a /tmp/packages.txt apt-get install -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/* /tmp/packages.txt

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

RUN chmod +x start.sh \
    && useradd --create-home --uid 1000 app \
    && chown -R app:app /app
USER app

EXPOSE 10000
CMD ["./start.sh"]
