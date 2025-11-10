from fastapi import Depends, HTTPException, Request
from jose import jwt, JWTError
from sqlmodel import Session
from app.auth import SECRET, ALGO
from app.db import engine
from app.models import User

def get_session():
    with Session(engine) as s:
        yield s

def get_current_user(
    request: Request,
    session: Session = Depends(get_session)
) -> User:
    # Cookie: access_token = <token>, or Authorization: Bearer <token>
    token = request.cookies.get("access_token")
    if not token:
        auth = request.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth.split(" ", 1)[1].strip()

    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        payload = jwt.decode(token, SECRET, algorithms=[ALGO])
        user_id = int(payload.get("sub", "0"))
    except (JWTError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid token")

    user = session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user
