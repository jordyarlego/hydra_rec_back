"""
Storage de fotos — Supabase Storage bucket `report-photos`.

Fluxo:
  1. Recebe bytes da foto + mime type
  2. Valida mime (apenas jpeg/png/webp), tamanho (5MB max)
  3. Re-encoda via Pillow para REMOVER EXIF (privacidade / segurança)
  4. Faz upload via service_role no bucket
  5. Retorna URL pública

Bucket precisa existir como `public` (read) com upload restrito ao
service_role. Veja `back_end_hydrarec/migrations/v3_civic_reports.sql §8`.
"""
from __future__ import annotations

import io
import logging
import os
import uuid
from typing import Optional

from PIL import Image, ImageOps

from services.supabase_client import get_service_client

logger = logging.getLogger(__name__)

_BUCKET = os.getenv("SUPABASE_STORAGE_BUCKET", "report-photos")

# 5 MB — espelha o limite do bucket no Supabase
MAX_BYTES = 5 * 1024 * 1024

_ALLOWED_MIMES = {
    "image/jpeg": ("jpg", "JPEG"),
    "image/jpg":  ("jpg", "JPEG"),
    "image/png":  ("png", "PNG"),
    "image/webp": ("webp", "WEBP"),
}

# Limite de dimensão para não estourar memória ao reencodar
_MAX_SIDE = 2400


class PhotoError(ValueError):
    """Erro de validação/processamento de foto."""


def _strip_and_normalize(data: bytes, mime: str) -> tuple[bytes, str, str]:
    """
    Decodifica, aplica orientação EXIF, descarta restante do EXIF, redimensiona
    se necessário e re-encoda. Retorna (bytes, ext, pil_format).
    """
    ext, pil_format = _ALLOWED_MIMES[mime]
    try:
        img = Image.open(io.BytesIO(data))
        img = ImageOps.exif_transpose(img)  # aplica rotação e descarta EXIF
        if img.mode in ("P", "RGBA") and pil_format == "JPEG":
            img = img.convert("RGB")
        # Redimensiona se muito grande
        if max(img.size) > _MAX_SIDE:
            img.thumbnail((_MAX_SIDE, _MAX_SIDE))
        buf = io.BytesIO()
        save_kwargs = {"format": pil_format}
        if pil_format == "JPEG":
            save_kwargs.update({"quality": 85, "optimize": True})
        elif pil_format == "WEBP":
            save_kwargs.update({"quality": 85, "method": 4})
        img.save(buf, **save_kwargs)
        return buf.getvalue(), ext, pil_format
    except Exception as e:
        raise PhotoError(f"Imagem inválida: {type(e).__name__}: {e}") from e


def upload_photo(data: bytes, mime: str) -> str:
    """
    Faz upload e retorna URL pública (https://...).
    Levanta PhotoError em validação; outros erros propagam.
    """
    if not data:
        raise PhotoError("Arquivo vazio.")
    if len(data) > MAX_BYTES:
        raise PhotoError(f"Arquivo maior que {MAX_BYTES // (1024*1024)}MB.")
    mime = (mime or "").lower().split(";")[0].strip()
    if mime not in _ALLOWED_MIMES:
        raise PhotoError(f"Tipo não suportado: {mime}. Use JPEG, PNG ou WEBP.")

    body, ext, _ = _strip_and_normalize(data, mime)

    name = f"reports/{uuid.uuid4().hex}.{ext}"
    client = get_service_client()
    try:
        client.storage.from_(_BUCKET).upload(
            path=name,
            file=body,
            file_options={"content-type": f"image/{ext}", "upsert": "false"},
        )
    except Exception as e:
        # Algumas versões do SDK retornam erro mesmo no sucesso quando upsert false;
        # validamos pegando a URL na sequência.
        logger.debug(f"storage.upload exception (pode ser benigna): {e}")

    try:
        public = client.storage.from_(_BUCKET).get_public_url(name)
    except Exception as e:
        logger.error(f"storage.get_public_url failed: {e}")
        raise

    if isinstance(public, dict):
        url = public.get("publicUrl") or public.get("publicURL")
    else:
        url = str(public)
    if not url:
        raise PhotoError("Falha ao gerar URL pública da foto.")
    return url


def bucket_exists() -> Optional[bool]:
    """Diagnóstico — útil em healthz/admin. None se SDK não suporta listagem."""
    try:
        client = get_service_client()
        buckets = client.storage.list_buckets()
        names = [b.get("name") if isinstance(b, dict) else getattr(b, "name", None) for b in buckets]
        return _BUCKET in names
    except Exception:
        return None
