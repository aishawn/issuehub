FROM python:3.13-slim
WORKDIR /app
RUN useradd --system --create-home --uid 10001 teamai \
    && mkdir -p /data /backups \
    && chown teamai:teamai /data /backups
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=teamai:teamai app ./app
COPY --chown=teamai:teamai web ./web
COPY --chown=teamai:teamai integrations ./integrations
COPY --chown=teamai:teamai teamai_cli.py mcp_server.py teamai_backup.py teamai_migrate.py teamai_transport.py README.md .env.example ./
ENV APP_DB=/data/teamai.db APP_HOST=0.0.0.0 APP_PORT=8787 PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER 10001:10001
EXPOSE 8787
CMD ["sh", "-c", "python -m uvicorn app.main:app --host ${APP_HOST} --port ${APP_PORT}"]
