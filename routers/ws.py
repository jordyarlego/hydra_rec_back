import asyncio
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from routers.dashboard import fetch_dashboard

router = APIRouter()


@router.websocket("/ws/{bairro}")
async def ws_bairro(websocket: WebSocket, bairro: str):
    await websocket.accept()
    try:
        while True:
            data = None
            try:
                data = await fetch_dashboard(bairro)
            except Exception:
                pass

            if data is not None:
                try:
                    await websocket.send_json(data)
                except WebSocketDisconnect:
                    break
                except RuntimeError:
                    break
                except Exception:
                    break

            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=300)
            except asyncio.TimeoutError:
                continue
            except WebSocketDisconnect:
                break
            except asyncio.CancelledError:
                raise
    except asyncio.CancelledError:
        raise
    except WebSocketDisconnect:
        pass
    except RuntimeError:
        pass
    except Exception:
        pass
