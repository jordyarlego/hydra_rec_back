import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from dotenv import load_dotenv

load_dotenv()

import asyncio
from routers import dashboard, narrative, healthz, reports, route

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


@app.on_event("startup")
async def startup():
    from workers.cron_alerts import start_cron
    asyncio.create_task(start_cron(300))

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")


@app.get("/manifest.json")
async def serve_manifest():
    return FileResponse(os.path.join(STATIC_DIR, "manifest.json"),
                        media_type="application/manifest+json")


@app.get("/sw.js")
async def serve_sw():
    return FileResponse(os.path.join(STATIC_DIR, "sw.js"),
                        media_type="application/javascript")


@app.get("/icon.svg")
async def serve_icon():
    return FileResponse(os.path.join(STATIC_DIR, "icon.svg"),
                        media_type="image/svg+xml")


@app.get("/")
async def serve_dashboard():
    path = os.path.join(STATIC_DIR, "index.html")
    if not os.path.exists(path):
        return {"message": "HydraRec API v2 online — frontend não encontrado em static/"}
    return FileResponse(path)
