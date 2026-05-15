import asyncio
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from routers.dashboard import fetch_dashboard

router = APIRouter()


@router.websocket("/ws/{bairro}")
async def ws_bairro(websocket: WebSocket, bairro: str):
    await websocket.accept()
    try:
        while True:
            try:
                data = await fetch_dashboard(bairro)
                await websocket.send_json(data)
            except Exception:
                pass
            await asyncio.sleep(300)  # 5 min — alinhado com cache Open-Meteo (15 min)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
