import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv

load_dotenv()

import asyncio
from routers import dashboard, narrative, healthz, reports, route, ws, push, forecast, apac

app = FastAPI(title="HydraRec API v2", version="2.0.0")

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
app.include_router(route.router)
app.include_router(ws.router)
app.include_router(push.router)
app.include_router(forecast.router)
app.include_router(apac.router)


@app.on_event("startup")
async def startup():
    from workers.cron_alerts import start_cron
    asyncio.create_task(start_cron(300))

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
