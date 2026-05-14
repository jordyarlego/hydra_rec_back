import time

_cache: dict = {}


def cache_get(key: str, ttl: int):
    entry = _cache.get(key)
    if entry and (time.time() - entry["ts"]) < ttl:
        return entry["data"]
    return None


def cache_set(key: str, data) -> None:
    _cache[key] = {"data": data, "ts": time.time()}


def cache_clear(key: str) -> None:
    _cache.pop(key, None)
