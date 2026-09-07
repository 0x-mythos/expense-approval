"""Password hashing.

pbkdf2_sha256 is pure-Python (no native build), which keeps the Docker image
small and avoids bcrypt version pitfalls. Swap to bcrypt/argon2 for production.
"""
from passlib.context import CryptContext

_pwd = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")


def hash_password(plain: str) -> str:
    return _pwd.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return _pwd.verify(plain, hashed)
