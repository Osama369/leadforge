FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    LF_ENV=production \
    DB_PATH=/app/data/leads.db \
    LF_ENV_FILE=/app/data/.env \
    OUTPUT_DIR=/app/data/output \
    FORWARDED_ALLOW_IPS=*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY leadforge ./leadforge
COPY config ./config

# run as an unprivileged user; /app/data is the persistent volume (DB, .env, backups, credentials)
RUN useradd --create-home --uid 1000 leadforge && mkdir -p /app/data && chown -R leadforge /app/data
USER leadforge
VOLUME ["/app/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"

CMD ["python", "-m", "leadforge", "serve", "--host", "0.0.0.0", "--port", "8000"]
