# @solarlead/api

FastAPI backend — agents + REST API for SolarLead.

## Layout

```
src/
├── main.py                # FastAPI app bootstrap
├── core/                  # Config, logging, DB, Redis, auth
│   ├── config.py
│   ├── logging.py
│   ├── database.py
│   ├── redis.py
│   ├── security.py
│   └── supabase_client.py
├── db/                    # SQLAlchemy models / repositories
│   └── models.py
├── models/                # Pydantic schemas (request/response)
│   ├── tenant.py
│   ├── territory.py
│   ├── roof.py
│   ├── subject.py
│   ├── lead.py
│   ├── campaign.py
│   └── event.py
├── routes/                # FastAPI routers
│   ├── auth.py
│   ├── tenants.py
│   ├── territories.py
│   ├── leads.py
│   ├── campaigns.py
│   ├── events.py
│   ├── webhooks.py
│   ├── public.py
│   └── admin.py
├── services/              # Business-logic services (cross-agent)
│   ├── scoring_service.py
│   ├── compliance_service.py
│   ├── storage_service.py
│   └── claude_service.py
├── agents/                # Agent modules (one per domain)
│   ├── base.py
│   ├── hunter.py
│   ├── identity.py
│   ├── scoring.py
│   ├── creative.py
│   ├── outreach.py
│   ├── tracking.py
│   └── compliance.py
└── workers/               # Background job workers (arq queues)
    ├── __init__.py
    └── main.py
```

## Quick Start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Run dev server
uvicorn src.main:app --reload --port 8000

# Run tests
pytest

# Lint + typecheck
ruff check src tests
mypy src
```

## API Docs

Once running, open:
- http://localhost:8000/docs (Swagger UI)
- http://localhost:8000/redoc (ReDoc)
