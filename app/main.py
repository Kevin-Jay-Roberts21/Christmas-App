from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.responses import RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from jose import jwt, JWTError
from sqlmodel import Session, select

from app.db import engine, init_db          # <-- keep this
from app.models import User, GiftList, Membership, Group, ListGroup, Claim, Item
from app.deps import get_session, get_current_user
from app.routers import users, lists, groups, claims
from app.auth import SECRET, ALGO

app = FastAPI(title="Christmas App")
templates = Jinja2Templates(directory="app/templates")

# Create tables if they don't exist (e.g., after deleting the DB file)
@app.on_event("startup")
def on_startup():
    init_db()   # <-- this line ensures 'user', 'giftlist', etc. are created

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

@app.get("/manifest.webmanifest", include_in_schema=False)
def manifest():
    return FileResponse(
        "app/static/manifest.webmanifest",
        media_type="application/manifest+json",
    )

@app.get("/sw.js", include_in_schema=False)
def service_worker():
    # Service worker must be served from the origin to control the whole app
    return FileResponse(
        "app/static/sw.js",
        media_type="application/javascript",
    )

# --- Routers ---
app.include_router(users.router)
app.include_router(lists.router)
app.include_router(groups.router)
app.include_router(claims.router)

# --- Home: redirect based on auth ---
@app.get("/", include_in_schema=False)
def home(request: Request):
    return templates.TemplateResponse("about.html", {"request": request})

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

                # --- The gifts you're giving (all items you have claimed anywhere) ---
        my_claims = s.exec(
            select(Claim).where(Claim.claimer_id == user.id)
        ).all()

        gifts_by_person = {}
        giftee_map = {}

        if my_claims:
            # All item ids you have claimed
            item_ids = {c.item_id for c in my_claims}

            # Fetch those items
            items = s.exec(
                select(Item).where(Item.id.in_(item_ids))
            ).all()
            item_by_id = {it.id: it for it in items}

            # Lists associated with those items
            list_ids = {it.list_id for it in items}
            lists_for_items = s.exec(
                select(GiftList).where(GiftList.id.in_(list_ids))
            ).all()
            list_by_id = {gl.id: gl for gl in lists_for_items}

            # Group items by the person who owns the list (the person you're gifting)
            giftee_ids = set()

            for cl in my_claims:
                item = item_by_id.get(cl.item_id)
                if not item:
                    continue
                gl = list_by_id.get(item.list_id)
                if not gl:
                    continue

                giftee_id = gl.owner_id
                giftee_ids.add(giftee_id)
                gifts_by_person.setdefault(giftee_id, []).append(item)

            # Fetch all those giftee users
            if giftee_ids:
                giftees = s.exec(
                    select(User).where(User.id.in_(giftee_ids))
                ).all()
                giftee_map = {u.id: u for u in giftees}

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
                "gifts_by_person": gifts_by_person,
                "giftee_map": giftee_map,
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
    
    # fetch my lists and groups for invites
    my_lists = session.exec(select(GiftList).where(GiftList.owner_id == me.id)).all()
    inv_group_ids = [m.group_id for m in invites]
    inv_groups = session.exec(select(Group).where(Group.id.in_(inv_group_ids))).all() if inv_group_ids else []
    inv_group_map = {g.id: g for g in inv_groups}
    return templates.TemplateResponse("my_groups.html", {"request": request, "me": me, "groups": groups, "mem_map": mem_map, "invites": invites, "my_lists": my_lists, "inv_group_map": inv_group_map})

@app.post("/account/delete")
def delete_account(
    request: Request,
    session: Session = Depends(get_session),
    me: User = Depends(get_current_user),
):
    # 1) Delete all groups this user leads
    leader_groups = session.exec(
        select(Group).where(Group.leader_id == me.id)
    ).all()
    for g in leader_groups:
        # delete memberships in this group
        mems = session.exec(
            select(Membership).where(Membership.group_id == g.id)
        ).all()
        for mem in mems:
            session.delete(mem)

        # delete list-group links
        lgs = session.exec(
            select(ListGroup).where(ListGroup.group_id == g.id)
        ).all()
        for lg in lgs:
            session.delete(lg)

        # delete all claims in this group
        claims = session.exec(
            select(Claim).where(Claim.group_id == g.id)
        ).all()
        for c in claims:
            session.delete(c)

        # finally delete the group itself
        session.delete(g)

    # 2) Remove this user from any other groups (as a member)
    mems = session.exec(
        select(Membership).where(Membership.user_id == me.id)
    ).all()
    for mem in mems:
        # remove this user's claims in that group
        claims = session.exec(
            select(Claim).where(
                Claim.group_id == mem.group_id,
                Claim.claimer_id == me.id,
            )
        ).all()
        for c in claims:
            session.delete(c)

        session.delete(mem)

    # 3) Delete all lists owned by this user (and their items + claims + links)
    user_lists = session.exec(
        select(GiftList).where(GiftList.owner_id == me.id)
    ).all()
    for gl in user_lists:
        # remove list-group links for this list
        lgs = session.exec(
            select(ListGroup).where(ListGroup.list_id == gl.id)
        ).all()
        for lg in lgs:
            session.delete(lg)

        # delete items and any claims on those items
        items = session.exec(
            select(Item).where(Item.list_id == gl.id)
        ).all()
        for it in items:
            item_claims = session.exec(
                select(Claim).where(Claim.item_id == it.id)
            ).all()
            for c in item_claims:
                session.delete(c)
            session.delete(it)

        session.delete(gl)

    # 4) Safety cleanup: any remaining claims made by this user
    stray_claims = session.exec(
        select(Claim).where(Claim.claimer_id == me.id)
    ).all()
    for c in stray_claims:
        session.delete(c)

    # 5) Delete the user account itself
    session.delete(me)
    session.commit()

    # 6) "Log out" by deleting the JWT cookie, just like /auth/logout
    resp = RedirectResponse(
        url="/auth/login?info=Your+account+has+been+deleted",
        status_code=303,
    )
    resp.delete_cookie("access_token")
    return resp

# Redirect 401s (e.g., from dependencies) to login for HTML routes
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if exc.status_code == 401:
        return RedirectResponse(url="/auth/login?info=Please+log+in", status_code=303)
    raise exc