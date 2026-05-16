import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv

load_dotenv()

import asyncio
from routers import dashboard, narrative, healthz, reports, ws, push, apac, weather, admin, official_data, ai_reports

app = FastAPI(title="HydraRec API v3 — Cívico", version="3.0.0")
_background_tasks: list[asyncio.Task] = []


def background_workers_enabled() -> bool:
    configured = os.getenv("ENABLE_BACKGROUND_WORKERS")
    if configured is not None:
        return configured.lower() in {"1", "true", "yes", "on"}
    return os.getenv("RENDER") == "true"

ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "http://localhost:5173,http://localhost:8000").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(dashboard.router)
app.include_router(narrative.router)
app.include_router(healthz.router)
app.include_router(reports.router)
app.include_router(ws.router)
app.include_router(push.router)
app.include_router(apac.router)
app.include_router(weather.router)
app.include_router(admin.router)
app.include_router(official_data.router)
app.include_router(ai_reports.router)


@app.get("/api/public-config")
async def public_config():
    return {
        "supabaseUrl": os.getenv("SUPABASE_URL", ""),
        "supabaseAnonKey": os.getenv("SUPABASE_KEY", ""),
    }


@app.on_event("startup")
async def startup():
    if not background_workers_enabled():
        return

    from workers.cron_alerts import start_cron
    from workers.ai_revalidation import start as start_ai_revalidation
    from workers.ticket_lifecycle import start as start_ticket_lifecycle
    _background_tasks.extend([
        asyncio.create_task(start_cron(300)),
        asyncio.create_task(start_ai_revalidation(60)),
        asyncio.create_task(start_ticket_lifecycle()),  # auto-close + monitora SLA
    ])


@app.on_event("shutdown")
async def shutdown():
    for task in _background_tasks:
        task.cancel()

    if _background_tasks:
        await asyncio.gather(*_background_tasks, return_exceptions=True)
        _background_tasks.clear()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
ASSETS_DIR = os.path.join(STATIC_DIR, "assets")

if os.path.isdir(ASSETS_DIR):
    app.mount("/assets", StaticFiles(directory=ASSETS_DIR), name="assets")


@app.get("/manifest.json")
async def serve_manifest():
    return FileResponse(os.path.join(STATIC_DIR, "manifest.json"),
                        media_type="application/manifest+json")


@app.get("/sw.js")
async def serve_sw():
    path = os.path.join(STATIC_DIR, "sw.js")
    if not os.path.exists(path):
        return {"message": "Service worker não gerado neste build"}
    return FileResponse(
        path,
        media_type="application/javascript",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/icon.svg")
async def serve_icon():
    return FileResponse(os.path.join(STATIC_DIR, "icon.svg"),
                        media_type="image/svg+xml")


@app.get("/")
async def serve_dashboard():
    path = os.path.join(STATIC_DIR, "index.html")
    if not os.path.exists(path):
        return {"message": "HydraRec API v2 online — frontend não encontrado em static/"}
    return FileResponse(path, headers={"Cache-Control": "no-store"})


@app.get("/admin")
@app.get("/admin/{path:path}")
async def serve_admin(path: str = ""):
    index = os.path.join(STATIC_DIR, "index.html")
    if not os.path.exists(index):
        return {"message": "HydraRec Admin indisponível — frontend não encontrado em static/"}
    return FileResponse(index, headers={"Cache-Control": "no-store"})
