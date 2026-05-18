import time
from collections import OrderedDict

# Cache bounded para não estourar memória no Render free (512MB).
# Cada entrada APAC pode carregar até ~170 stations. 16 entradas dão folga
# pros 3 endpoints APAC + algum extra do dashboard sem inflar RAM.
_MAX_ENTRIES = 16
_cache: "OrderedDict[str, dict]" = OrderedDict()


def cache_get(key: str, ttl: int):
    """Retorna dados se dentro do TTL, None caso contrário."""
    entry = _cache.get(key)
    if entry and (time.time() - entry["ts"]) < ttl:
        # Move para o final (LRU recente)
        _cache.move_to_end(key)
        return entry["data"]
    return None


def cache_get_stale(key: str):
    """Retorna dados mesmo após expirar o TTL — para fallback quando API cai."""
    entry = _cache.get(key)
    if entry:
        age_min = round((time.time() - entry["ts"]) / 60)
        data = dict(entry["data"]) if isinstance(entry["data"], dict) else entry["data"]
        if isinstance(data, dict):
            data["_stale"] = True
            data["_stale_age_min"] = age_min
        return data
    return None


def cache_set(key: str, data) -> None:
    _cache[key] = {"data": data, "ts": time.time()}
    _cache.move_to_end(key)
    while len(_cache) > _MAX_ENTRIES:
        _cache.popitem(last=False)  # remove o mais antigo (FIFO)


def cache_clear(key: str) -> None:
    _cache.pop(key, None)
