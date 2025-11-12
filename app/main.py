from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from jose import jwt, JWTError
from sqlmodel import Session, select

from app.db import engine, init_db
from app.models import User, GiftList, Membership, Group, ListGroup
from app.deps import get_session, get_current_user
from app.routers import users, lists, groups, claims
from app.auth import SECRET, ALGO

app = FastAPI(title="Christmas App")
templates = Jinja2Templates(directory="app/templates")

# --- Attach current user to request.state.user (optional, convenient) ---
class AuthStateMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request.state.user = None
        token = request.cookies.get("access_token")
        if token:
            try:
                payload = jwt.decode(token, SECRET, algorithms=[ALGO])
                user_id = int(payload.get("sub", "0"))
                with Session(engine) as s:
                    u = s.get(User, user_id)
                    if u:
                        request.state.user = u
            except (JWTError, ValueError):
                pass
        return await call_next(request)

app.add_middleware(AuthStateMiddleware)

# --- Static files ---
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# --- Routers ---
app.include_router(users.router)
app.include_router(lists.router)
app.include_router(groups.router)
app.include_router(claims.router)

# --- Home: redirect based on auth ---
@app.get("/")
def home(request: Request):
    if request.state.user:
        return RedirectResponse(url="/account", status_code=303)
    return RedirectResponse(url="/auth/login", status_code=303)

# --- Simple account dashboard (uses your templates) ---

@app.get("/account")
def account(request: Request):
    user = request.state.user
    if not user:
        return RedirectResponse(url="/auth/login", status_code=303)

    with Session(engine) as s:
        my_lists = s.exec(select(GiftList).where(GiftList.owner_id == user.id)).all()

        # All memberships for the current user, build mem_map
        my_mems = s.exec(select(Membership).where(Membership.user_id == user.id)).all()
        mem_map = {m.group_id: m for m in my_mems}

        from sqlalchemy import or_, and_
        subq = select(Membership.group_id).where(
            Membership.user_id == user.id,
            or_(
                Membership.is_approved == True,
                and_(
                    Membership.is_approved == False,
                    Membership.is_denied == False,
                    Membership.is_invite == False  # pending request (not invite)
                )
            )
        )
        my_groups = s.exec(select(Group).where(Group.id.in_(subq))).all()

        list_for_group = {}
        groups_for_list = {}

        for g in my_groups:
            mem = mem_map.get(g.id)
            if mem and mem.selected_list_id:
                gl = s.get(GiftList, mem.selected_list_id)
                if gl and gl.owner_id == user.id:
                    list_for_group[g.id] = gl
                groups_for_list.setdefault(mem.selected_list_id, []).append(g)

        return templates.TemplateResponse(
            "dashboard.html",
            {
                "request": request,
                "me": user,
                "lists": my_lists,
                "groups": my_groups,
                "groups_for_list": groups_for_list,
                "list_for_group": list_for_group,
                "mem_map": mem_map,
            },
        )


@app.get("/about")
def about(request: Request):
    return templates.TemplateResponse("about.html", {"request": request})


@app.get("/lists")
def my_lists(
    request: Request,
    session: Session = Depends(get_session),
    me: User = Depends(get_current_user),
):
    lists = session.exec(select(GiftList).where(GiftList.owner_id == me.id)).all()
    return templates.TemplateResponse(
        "my_lists.html",
        {"request": request, "me": me, "lists": lists},
    )


@app.get("/groups")
def my_groups(request: Request, session: Session = Depends(get_session), me: User = Depends(get_current_user)):
    # all memberships for this user
    memberships = session.exec(select(Membership).where(Membership.user_id == me.id)).all()
    # invitations are those marked is_invite == True
    invites = [m for m in memberships if getattr(m, "is_invite", False) and not m.is_denied and not m.is_approved]
    # groups to display under "you're in" exclude invitations; pending shown only for true join-requests
    non_invite_mems = [m for m in memberships if not getattr(m, "is_invite", False)]
    group_ids = [m.group_id for m in non_invite_mems]
    groups = session.exec(select(Group).where(Group.id.in_(group_ids))).all() if group_ids else []
    mem_map = {m.group_id: m for m in memberships}
    return templates.TemplateResponse("my_groups.html", {"request": request, "me": me, "groups": groups, "mem_map": mem_map, "invites": invites})


# Redirect 401s (e.g., from dependencies) to login for HTML routes
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if exc.status_code == 401:
        return RedirectResponse(url="/auth/login?info=Please+log+in", status_code=303)
    raise exc
