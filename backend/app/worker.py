"""
ARQ worker process.
Run with: arq app.worker.WorkerSettings

This process handles:
- RCA jobs (retried up to 3 times with backoff if Claude/DB fails)
- Periodic: silent node detection every 2 min
- Periodic: incident auto-resolve every 5 min
"""
from arq.connections import RedisSettings

from app.core.config import get_settings
from app.tasks.rca import run_rca_task, check_silent_nodes_task
from app.tasks.autoresolve import auto_resolve_incidents

settings = get_settings()

# Parse Redis URL for ARQ (it needs host/port separately)
def _redis_settings() -> RedisSettings:
    url = settings.REDIS_URL
    # redis://host:port or redis://:password@host:port
    url = url.replace("redis://", "")
    if "@" in url:
        _, url = url.split("@", 1)
    host, port = url.split(":") if ":" in url else (url, "6379")
    return RedisSettings(host=host, port=int(port))


class WorkerSettings:
    functions = [run_rca_task]

    cron_jobs = [
        # Check for silent nodes every 2 minutes
        {
            "name": "check_silent_nodes",
            "coroutine": check_silent_nodes_task,
            "minute": {i for i in range(0, 60, 2)},
        },
        # Auto-resolve quiet incidents every 5 minutes
        {
            "name": "auto_resolve",
            "coroutine": auto_resolve_incidents,
            "minute": {i for i in range(0, 60, 5)},
        },
    ]

    redis_settings = _redis_settings()

    max_jobs = 20
    job_timeout = 180          # RCA must complete within 3 minutes
    keep_result = 3600         # keep job results for 1 hour
    retry_jobs = True
    max_tries = 3              # retry failed RCA jobs up to 3 times
    poll_delay = 0.5
