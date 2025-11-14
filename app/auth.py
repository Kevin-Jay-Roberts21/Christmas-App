from datetime import datetime, timedelta
import os
from jose import jwt
from passlib.hash import pbkdf2_sha256

SECRET = os.getenv("CHRISTMAS_SECRET", "change-me")  # set in env for production
ALGO = "HS256"
TOKEN_MINUTES = int(os.getenv("TOKEN_MINUTES", str(60 * 24 * 14)))

def hash_pwd(p: str) -> str:
    return pbkdf2_sha256.hash(p)

def verify_pwd(p: str, h: str) -> bool:
    return pbkdf2_sha256.verify(p, h)

def make_token(sub: int, minutes: int = TOKEN_MINUTES) -> str:
    now = datetime.utcnow()
    payload = {"sub": str(sub), "iat": now, "exp": now + timedelta(minutes=minutes)}
    return jwt.encode(payload, SECRET, algorithm=ALGO)
