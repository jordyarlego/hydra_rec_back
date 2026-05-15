import time

_cache: dict = {}


def cache_get(key: str, ttl: int):
    """Retorna dados se dentro do TTL, None caso contrário."""
    entry = _cache.get(key)
    if entry and (time.time() - entry["ts"]) < ttl:
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


def cache_clear(key: str) -> None:
    _cache.pop(key, None)
