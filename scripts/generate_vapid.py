"""
Gera VAPID keys compatíveis com pywebpush/py_vapid.

Uso:
    cd back_end_hydrarec
    source venv/bin/activate
    python scripts/generate_vapid.py
"""
import base64
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def main():
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_key = private_key.public_key()

    raw_public = public_key.public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    private_value = private_key.private_numbers().private_value
    raw_private = private_value.to_bytes(32, "big")

    pub_b64 = _b64url(raw_public)
    priv_b64 = _b64url(raw_private)

    print("=" * 70)
    print("VAPID keys geradas com sucesso!")
    print("=" * 70)
    print()
    print("Cole essas variáveis no .env local E no Render:")
    print()
    print(f"VAPID_PUBLIC_KEY={pub_b64}")
    print(f"VAPID_PRIVATE_KEY={priv_b64}")
    print(f"VAPID_EMAIL=jordyarlego@gmail.com")
    print()
    print("=" * 70)
    print(f"Tamanho public_key:  {len(raw_public)} bytes (deve ser 65)")
    print(f"Tamanho private_key: {len(raw_private)} bytes (deve ser 32)")
    print("=" * 70)


if __name__ == "__main__":
    main()
