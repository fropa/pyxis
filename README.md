# Pyxis

AI-powered infrastructure observability — log ingestion, distributed tracing, anomaly detection, and root cause analysis via Claude.

## What it does

- **Detects anomalies** in logs and traces automatically
- **Opens incidents** and runs AI root cause analysis (RCA) using Claude
- **APM / Tracing** — tracks latency, p99, error rates per service; fires incidents on spikes
- **Deployment correlation** — links incidents to recent deploys
- **Runbook generator** — auto-writes a runbook when an incident resolves
- **Log playground** — paste any logs, get instant AI analysis
- **Multi-tenant** — one backend serves multiple teams

## Quick install (any Linux)

```bash
curl -fsSL https://raw.githubusercontent.com/fropa/pyxis/main/install.sh | bash
```

The script will:
1. Install Docker if not present (Ubuntu, Debian, RHEL, Fedora, Arch)
2. Clone this repo
3. Ask for your Anthropic API key
4. Start the full stack
5. Create a default tenant and print your API key

## Manual install

**Requirements:** Docker, Docker Compose

```bash
git clone https://github.com/fropa/pyxis.git
cd pyxis
cp backend/.env.example backend/.env
# Edit backend/.env and set ANTHROPIC_API_KEY
docker compose up -d --build
```

Then create a tenant to get your API key:

```bash
curl -X POST http://localhost:8000/api/v1/tenants/ \
  -H "Content-Type: application/json" \
  -d '{"name":"default"}'
```

Open **http://localhost:5173** and paste the `api_key` from the response into Settings.

## Services

| Service    | URL                         |
|------------|-----------------------------|
| Dashboard  | http://localhost:5173        |
| API        | http://localhost:8000        |
| API docs   | http://localhost:8000/docs   |
| PostgreSQL | localhost:5432               |
| Redis      | localhost:6379               |

## Sending data

**Logs** (from any host, script, or CI pipeline):

```bash
curl -X POST http://localhost:8000/api/v1/ingest/ \
  -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "events": [{
      "source": "app_log",
      "level":  "error",
      "node_name": "web-01",
      "raw": "FATAL: database connection timeout after 30s"
    }]
  }'
```

**Traces / APM** (from any service):

```bash
curl -X POST http://localhost:8000/api/v1/traces/ \
  -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "spans": [{
      "trace_id":    "abc123",
      "span_id":     "def456",
      "service":     "api-gateway",
      "operation":   "GET /api/users",
      "duration_ms": 245,
      "status":      "ok",
      "status_code": 200
    }]
  }'
```

**Deploy events** (trigger correlation in RCA):

```bash
curl -X POST http://localhost:8000/api/v1/deploy-events/ \
  -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{"service":"api-gateway","version":"v1.4.2","deployed_by":"ci"}'
```

## Install agent on a Linux host

```bash
curl -fsSL http://YOUR_SERVER:8000/install | bash -s -- YOUR_KEY
```

The agent continuously ships logs and heartbeats from the host.

## Stack

| Layer     | Technology                          |
|-----------|-------------------------------------|
| Backend   | Python, FastAPI, SQLAlchemy, ARQ    |
| AI        | Claude (Anthropic API)              |
| Database  | PostgreSQL + pgvector               |
| Cache     | Redis                               |
| Frontend  | React, Vite, Tailwind, Recharts     |
| Deploy    | Docker Compose / Helm (Kubernetes)  |

## Environment variables

| Variable            | Description                    | Required |
|---------------------|--------------------------------|----------|
| `ANTHROPIC_API_KEY` | Anthropic API key for Claude   | Yes      |
| `DATABASE_URL`      | PostgreSQL connection string   | Yes      |
| `REDIS_URL`         | Redis connection string        | Yes      |
| `SECRET_KEY`        | Random secret for signing      | Yes      |
| `DEBUG`             | Enable verbose SQL logging     | No       |

## License

MIT
