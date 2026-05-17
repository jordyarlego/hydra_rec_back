#!/usr/bin/env python3
"""Create or update the local HydraRec admin user in Supabase Auth."""

from __future__ import annotations

import json
import os
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def request(method: str, url: str, service_key: str, payload: dict | None = None) -> tuple[int, dict]:
    body = None
    headers = {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Content-Type": "application/json",
    }
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    context = ssl.create_default_context(cafile=_certifi_ca())
    try:
        with urllib.request.urlopen(req, timeout=20, context=context) as res:
            data = res.read().decode("utf-8") or "{}"
            return res.status, json.loads(data)
    except urllib.error.HTTPError as exc:
        data = exc.read().decode("utf-8") or "{}"
        try:
            parsed = json.loads(data)
        except json.JSONDecodeError:
            parsed = {"message": data}
        return exc.code, parsed


def _certifi_ca() -> str | None:
    try:
        import certifi

        return certifi.where()
    except Exception:
        return None


def find_user(base_url: str, service_key: str, email: str) -> dict | None:
    status, data = request("GET", f"{base_url}/auth/v1/admin/users?page=1&per_page=1000", service_key)
    if status >= 400:
        raise RuntimeError(data.get("message") or data.get("msg") or "Falha ao listar usuários.")
    users = data.get("users") if isinstance(data, dict) else None
    for user in users or []:
        if str(user.get("email", "")).lower() == email.lower():
            return user
    return None


def main() -> int:
    if len(sys.argv) != 3:
        print("Uso: scripts/ensure_admin_user.py <email> <senha>", file=sys.stderr)
        return 2

    load_env(Path(__file__).resolve().parents[1] / ".env")
    supabase_url = os.getenv("SUPABASE_URL", "").rstrip("/")
    service_key = os.getenv("SUPABASE_SERVICE_KEY", "")
    email, password = sys.argv[1], sys.argv[2]

    if not supabase_url or not service_key:
        print("SUPABASE_URL e SUPABASE_SERVICE_KEY são obrigatórios.", file=sys.stderr)
        return 2

    payload = {
        "email": email,
        "password": password,
        "email_confirm": True,
        "user_metadata": {"role": "admin"},
        "app_metadata": {"role": "admin"},
    }

    status, data = request("POST", f"{supabase_url}/auth/v1/admin/users", service_key, payload)
    if 200 <= status < 300:
        print(f"Admin criado: {email}")
        return 0

    user = find_user(supabase_url, service_key, email)
    if not user:
        print(data.get("message") or data.get("msg") or "Falha ao criar admin.", file=sys.stderr)
        return 1

    user_id = user["id"]
    status, data = request("PUT", f"{supabase_url}/auth/v1/admin/users/{user_id}", service_key, payload)
    if status >= 400:
        print(data.get("message") or data.get("msg") or "Falha ao atualizar admin.", file=sys.stderr)
        return 1

    print(f"Admin atualizado: {email}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
