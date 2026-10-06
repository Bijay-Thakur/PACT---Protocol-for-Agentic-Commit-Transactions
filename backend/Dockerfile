FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv/pact

COPY pyproject.toml alembic.ini requirements-constraints.txt ./
COPY app ./app
COPY migrations ./migrations
RUN pip install --no-cache-dir -c requirements-constraints.txt .

EXPOSE 8000
# Migrations run on every start (idempotent), then the API.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
