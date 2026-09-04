"""FastAPI dashboard and scheduler for the Edge 1st paper bot."""

from __future__ import annotations

import asyncio
import os
import sys
from contextlib import asynccontextmanager
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse

HERE = Path(__file__).resolve().parent
load_dotenv(HERE / ".env")
DASHBOARD = HERE / "edge_1st_dashboard.html"
PYTHON = os.environ.get("PYTHON_EXECUTABLE", sys.executable)
REFRESH_SECONDS = max(60, int(os.environ.get("EDGE1ST_REFRESH_SECONDS", "300")))
OFF_HOURS_REFRESH_SECONDS = max(
    REFRESH_SECONDS, int(os.environ.get("EDGE1ST_OFF_HOURS_REFRESH_SECONDS", "21600"))
)
MARKET_TZ = ZoneInfo("Asia/Kolkata")
MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 35)

_run_lock = asyncio.Lock()
_scheduler_task: asyncio.Task | None = None
_status: dict[str, object] = {
    "state": "starting",
    "last_started_at": None,
    "last_completed_at": None,
    "last_exit_code": None,
    "last_error": None,
}


def _now_iso() -> str:
    return datetime.now(MARKET_TZ).isoformat()


def _market_is_open() -> bool:
    now = datetime.now(MARKET_TZ)
    return now.weekday() < 5 and MARKET_OPEN <= now.time() <= MARKET_CLOSE


async def run_edge_bot() -> int:
    """Refresh reports in a child process without blocking web requests."""
    if _run_lock.locked():
        return int(_status.get("last_exit_code") or 0)

    async with _run_lock:
        _status.update(state="running", last_started_at=_now_iso(), last_error=None)
        env = os.environ.copy()
        env["EDGE1ST_NO_BROWSER"] = "1"
        try:
            process = await asyncio.create_subprocess_exec(
                PYTHON,
                str(HERE / "edge_1st_bot.py"),
                cwd=str(HERE),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            output, _ = await process.communicate()
            if output:
                print(output.decode(errors="replace"), flush=True)
            exit_code = int(process.returncode or 0)
            _status.update(
                state="ready" if exit_code == 0 else "error",
                last_completed_at=_now_iso(),
                last_exit_code=exit_code,
                last_error=None if exit_code == 0 else f"bot exited with code {exit_code}",
            )
            return exit_code
        except Exception as exc:
            _status.update(
                state="error",
                last_completed_at=_now_iso(),
                last_exit_code=-1,
                last_error=f"{type(exc).__name__}: {exc}",
            )
            print(f"scheduled refresh failed: {exc}", flush=True)
            return -1


async def _scheduler() -> None:
    while True:
        await run_edge_bot()
        delay = REFRESH_SECONDS if _market_is_open() else OFF_HOURS_REFRESH_SECONDS
        await asyncio.sleep(delay)


@asynccontextmanager
async def lifespan(_: FastAPI):
    global _scheduler_task
    _scheduler_task = asyncio.create_task(_scheduler(), name="edge-1st-scheduler")
    yield
    _scheduler_task.cancel()
    try:
        await _scheduler_task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="Edge 1st Dashboard", lifespan=lifespan)


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def dashboard():
    if not DASHBOARD.exists():
        return HTMLResponse(
            "<h1>Edge 1st is starting</h1><p>The first data refresh is still running.</p>",
            status_code=202,
        )
    return FileResponse(DASHBOARD, media_type="text/html", headers={"Cache-Control": "no-store"})


@app.get("/healthz")
async def health():
    return {"ok": True, "scheduler": _status["state"]}


@app.get("/api/status")
async def status():
    return {
        **_status,
        "market_open": _market_is_open(),
        "refresh_seconds": REFRESH_SECONDS,
        "dashboard_ready": DASHBOARD.exists(),
        "analytics_token_configured": bool(os.environ.get("UPSTOX_ANALYTICS_TOKEN")),
    }

