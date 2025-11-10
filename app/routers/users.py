from typing import Optional, Dict
from urllib.parse import urlencode
from fastapi import APIRouter, Depends, Form, Response, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from sqlalchemy import or_

from app.deps import get_session
from app.models import User
from app.auth import hash_pwd, make_token, verify_pwd

router = APIRouter(prefix="/auth", tags=["auth"])
templates = Jinja2Templates(directory="app/templates")


# --- Helpers ---------------------------------------------------------------

def redirect_get(url: str, params: Optional[Dict[str, str]] = None) -> RedirectResponse:
    if params:
        qs = urlencode(params, doseq=False)
        url = f"{url}?{qs}"
    return RedirectResponse(url=url, status_code=303)


# --- Forms ----------------------------------------------------------------

@router.get("/login")
def login_form(request: Request, error: Optional[str] = None, info: Optional[str] = None):
    # error / info come via query params, e.g. ?error=Username+already+taken
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "error": error, "info": info},
    )


@router.get("/signup")
def signup_form(
    request: Request,
    error: Optional[str] = None,
    email: Optional[str] = None,
    username: Optional[str] = None,
):
    # Prefill fields if provided via query params
    return templates.TemplateResponse(
        "signup.html",
        {"request": request, "error": error, "email": email or "", "username": username or ""},
    )


# --- Actions --------------------------------------------------------------

@router.post("/login")
def login(
    response: Response,
    username: str = Form(...),   # can be username OR email
    password: str = Form(...),
    session: Session = Depends(get_session),
):
    ident = username.strip()
    from sqlmodel import or_
    user = session.exec(
        select(User).where(or_(User.username == ident, User.email == ident))
    ).first()
    if not user or not verify_pwd(password, user.password_hash):
        return redirect_get("/auth/login", {"error": "Invalid username/email or password"})
    token = make_token(user.id)
    resp = redirect_get("/account")
    resp.set_cookie("access_token", token, httponly=True, samesite="lax")
    return resp


@router.post("/signup")
def signup(
    response: Response,
    email: str = Form(...),
    username: str = Form(...),
    password: str = Form(...),
    session: Session = Depends(get_session),
):
    # Normalize for comparison (optional but recommended)
    email_norm = email.strip()
    username_norm = username.strip()

    if session.exec(select(User).where(User.username == username_norm)).first():
        # Stay on signup with error, keep their inputs
        return redirect_get(
            "/auth/signup",
            {"error": "Username already taken", "email": email_norm, "username": username_norm},
        )

    if session.exec(select(User).where(User.email == email_norm)).first():
        return redirect_get(
            "/auth/signup",
            {"error": "Email already registered", "email": email_norm, "username": username_norm},
        )

    u = User(email=email_norm, username=username_norm, password_hash=hash_pwd(password))
    session.add(u)
    session.commit()
    session.refresh(u)

    token = make_token(u.id)
    resp = redirect_get("/account")
    resp.set_cookie("access_token", token, httponly=True, samesite="lax")
    return resp


@router.post("/logout")
def logout():
    resp = redirect_get("/auth/login?info=You+have+been+logged+out")
    resp.delete_cookie("access_token")
    return resp
