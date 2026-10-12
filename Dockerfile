FROM python:3.12.14-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATABASE=/app/instance/cases.sqlite3 \
    PORT=8000

WORKDIR /app

RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --no-create-home app \
    && mkdir -p /app/instance /var/data \
    && chown app:app /app/instance /var/data

COPY requirements.txt ./
# Optional CA bundle for environments with an organizational HTTPS proxy.
# The trusted certificate stays in a temporary BuildKit secret, not the image.
RUN --mount=type=secret,id=pip_cert \
    if [ -f /run/secrets/pip_cert ]; then export PIP_CERT=/run/secrets/pip_cert; fi \
    && pip install --disable-pip-version-check --no-cache-dir -r requirements.txt

COPY --chown=app:app . .

USER app
EXPOSE 8000

CMD ["sh", "-c", "exec gunicorn --bind 0.0.0.0:${PORT:-8000} --workers 1 --threads 4 --timeout 120 --graceful-timeout 30 --access-logfile - --error-logfile - app:app"]
