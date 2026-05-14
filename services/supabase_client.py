import os
from supabase import create_client, Client

_client: Client | None = None
_service_client: Client | None = None


def get_client() -> Client:
    global _client
    if _client is None:
        url = os.getenv("SUPABASE_URL", "")
        key = os.getenv("SUPABASE_KEY", "")
        if not url or not key:
            raise RuntimeError("SUPABASE_URL e SUPABASE_KEY são obrigatórios")
        _client = create_client(url, key)
    return _client


def get_service_client() -> Client:
    """Usar apenas em operações privilegiadas de backend — nunca expor ao frontend."""
    global _service_client
    if _service_client is None:
        url = os.getenv("SUPABASE_URL", "")
        key = os.getenv("SUPABASE_SERVICE_KEY", "")
        if not url or not key:
            raise RuntimeError("SUPABASE_URL e SUPABASE_SERVICE_KEY são obrigatórios")
        _service_client = create_client(url, key)
    return _service_client
