from fastapi import FastAPI, Request, Depends
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
        my_groups = s.exec(
            select(Group).where(Group.id.in_(
                select(Membership.group_id).where(Membership.user_id == user.id)
            ))
        ).all()

        # which lists are visible in which groups
        list_for_group = {}
        groups_for_list = {}
        for g in my_groups:
            glinks = s.exec(select(ListGroup).where(ListGroup.group_id == g.id)).all()
            for link in glinks:
                gl = s.get(GiftList, link.list_id)
                if gl:
                    groups_for_list.setdefault(gl.id, []).append(g)
                    list_for_group[g.id] = gl

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "me": user,
            "lists": my_lists,
            "groups": my_groups,
            "groups_for_list": groups_for_list,
            "list_for_group": list_for_group,
        },
    )

# --- Startup ---
@app.on_event("startup")
def on_startup():
    init_db()


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
def my_groups(
    request: Request,
    session: Session = Depends(get_session),
    me: User = Depends(get_current_user),
):
    memberships = session.exec(select(Membership).where(Membership.user_id == me.id)).all()
    group_ids = [m.group_id for m in memberships]
    groups = (
        session.exec(select(Group).where(Group.id.in_(group_ids))).all()
        if group_ids
        else []
    )
    return templates.TemplateResponse(
        "my_groups.html",
        {"request": request, "me": me, "groups": groups},
    )
