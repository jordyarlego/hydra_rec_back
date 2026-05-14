import hashlib
import os


_SALT = os.getenv("IP_HASH_SALT", "hydrarec-v2-default-change-me")


def hash_ip(ip: str) -> str:
    return hashlib.sha256((_SALT + ip).encode()).hexdigest()[:32]
