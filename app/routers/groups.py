from typing import Optional, Dict
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select, or_
from app.deps import get_session, get_current_user
from app.models import Group, Membership, ListGroup, GiftList, Item, User

router = APIRouter(prefix="/groups", tags=["groups"])
templates = Jinja2Templates(directory="app/templates")

def redirect_get(url: str, qs: Optional[Dict[str, str]] = None) -> RedirectResponse:
    if qs:
        from urllib.parse import urlencode
        url = f"{url}?{urlencode(qs)}"
    return RedirectResponse(url=url, status_code=303)

# ---------- Create ---------------------------------------------------------

@router.get("/new")
def group_new_form(
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user)
):
    my_lists = session.exec(select(GiftList).where(GiftList.owner_id == user.id)).all()
    return templates.TemplateResponse("group_new.html", {"request": request, "me": user, "my_lists": my_lists})

@router.post("/new")
def group_new(
    name: str = Form(...),
    selected_list_id: int = Form(...),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    if session.exec(select(Group).where(Group.name == name)).first():
        raise HTTPException(400, "Group name already exists")
    gl = session.get(GiftList, selected_list_id)
    if not gl or gl.owner_id != user.id:
        raise HTTPException(400, "Invalid list selection")

    g = Group(name=name, leader_id=user.id)  # leader = creator
    session.add(g)
    session.commit(); session.refresh(g)

    # leader auto-membership approved
    session.add(Membership(group_id=g.id, user_id=user.id, selected_list_id=gl.id, is_approved=True))
    # show leader's list
    if not session.exec(select(ListGroup).where(ListGroup.group_id == g.id, ListGroup.list_id == gl.id)).first():
        session.add(ListGroup(group_id=g.id, list_id=gl.id))
    session.commit()

    return redirect_get(f"/groups/{g.id}")   # ✅ redirect after create

# ---------- Search ---------------------------------------------------------

@router.get("/search")
def group_search_form(request: Request, user: User = Depends(get_current_user)):
    return templates.TemplateResponse("group_search.html", {"request": request, "me": user, "results": None})

@router.post("/search")
def group_search(
    q: str = Form(...),
    request: Request = None,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    q = q.strip()
    results = []
    if q.isdigit():
        g = session.get(Group, int(q))
        if g: results = [g]
    else:
        results = session.exec(select(Group).where(Group.name.ilike(f"%{q}%"))).all()
    return templates.TemplateResponse("group_search.html", {"request": request, "me": user, "results": results, "q": q})

# ---------- Join (request) -------------------------------------------------

@router.get("/join")
def group_join_form(
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user)
):
    my_lists = session.exec(select(GiftList).where(GiftList.owner_id == user.id)).all()
    error = request.query_params.get("error")
    info = request.query_params.get("info")
    return templates.TemplateResponse("group_join.html", {"request": request, "me": user, "my_lists": my_lists, "error": error, "info": info})

@router.post("/join")
def group_join_request(
    group_identifier: str = Form(...),
    selected_list_id: int = Form(...),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    # Accept either numeric ID or exact group name
    g = None
    ident = group_identifier.strip()
    if ident.isdigit():
        g = session.get(Group, int(ident))
    if not g:
        g = session.exec(select(Group).where(Group.name == ident)).first()
    if not g:
        return redirect_get("/groups/join", {"error": "This group does not exist."})

    gl = session.get(GiftList, selected_list_id)
    if not gl or gl.owner_id != user.id:
        return redirect_get("/groups/join", {"error": "Invalid list selection."})

    mem = session.exec(select(Membership).where(Membership.group_id == g.id, Membership.user_id == user.id)).first()
    if mem:
        # update their selected list, keep approval state
        mem.selected_list_id = gl.id
    else:
        mem = Membership(group_id=g.id, user_id=user.id, selected_list_id=gl.id, is_approved=False)
        session.add(mem)
    session.commit()
    return redirect_get(f"/groups/{g.id}", {"info": "Join request sent. Waiting for leader approval."})

# ---------- Leader manage: approve / invite --------------------------------

@router.get("/{group_id}/manage")
def manage_group(
    group_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    g = session.get(Group, group_id)
    if not g: raise HTTPException(404, "Group not found")
    if g.leader_id != user.id: raise HTTPException(403, "Only the leader can manage the group")

    pending = session.exec(select(Membership).where(Membership.group_id == g.id, Membership.is_approved == False)).all()  # noqa: E712
    approved = session.exec(select(Membership).where(Membership.group_id == g.id, Membership.is_approved == True)).all()  # noqa: E712

    # map users and lists
    def user_of(mem): return session.get(User, mem.user_id)
    def list_of(mem): return session.get(GiftList, mem.selected_list_id) if mem.selected_list_id else None

    return templates.TemplateResponse(
        "group_manage.html",
        {
            "request": request,
            "me": user,
            "group": g,
            "pending": [(mem, user_of(mem), list_of(mem)) for mem in pending],
            "approved": [(mem, user_of(mem), list_of(mem)) for mem in approved],
        },
    )

@router.post("/{group_id}/approve")
def approve_member(
    group_id: int,
    membership_id: int = Form(...),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    g = session.get(Group, group_id)
    if not g or g.leader_id != user.id: raise HTTPException(403, "Leader only")
    mem = session.get(Membership, membership_id)
    if not mem or mem.group_id != g.id: raise HTTPException(404, "Membership not found")
    mem.is_approved = True
    # ensure their list is visible in group
    if mem.selected_list_id and not session.exec(
        select(ListGroup).where(ListGroup.group_id == g.id, ListGroup.list_id == mem.selected_list_id)
    ).first():
        session.add(ListGroup(group_id=g.id, list_id=mem.selected_list_id))
    session.commit()
    return redirect_get(f"/groups/{g.id}/manage")

@router.post("/{group_id}/invite")
def invite_member(
    group_id: int,
    identifier: str = Form(...),  # username or email
    selected_list_id: int = Form(None),  # optional for invitee (can set later)
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    g = session.get(Group, group_id)
    if not g or g.leader_id != user.id: raise HTTPException(403, "Leader only")
    invited = session.exec(
        select(User).where(or_(User.username == identifier, User.email == identifier))
    ).first()
    if not invited: raise HTTPException(404, "User not found")

    mem = session.exec(select(Membership).where(Membership.group_id == g.id, Membership.user_id == invited.id)).first()
    if not mem:
        mem = Membership(group_id=g.id, user_id=invited.id, selected_list_id=selected_list_id, is_approved=True)
        session.add(mem)
    else:
        mem.is_approved = True
        if selected_list_id: mem.selected_list_id = selected_list_id
    session.commit()
    return redirect_get(f"/groups/{g.id}/manage", {"info": "User invited."})

# ---------- Group view -----------------------------------------------------

@router.get("/{group_id}")
def group_view(
    group_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
    info: Optional[str] = None,   # <-- was: info: str | None = None
):
    g = session.get(Group, group_id)
    if not g: raise HTTPException(404, "Group not found")

    # user must be approved or be the leader
    mem = session.exec(
        select(Membership).where(Membership.group_id == g.id, Membership.user_id == user.id)
    ).first()
    if not (user.id == g.leader_id or (mem and mem.is_approved)):
        raise HTTPException(403, "Not approved to view this group")

    # visible lists in group
    links = session.exec(select(ListGroup).where(ListGroup.group_id == g.id)).all()
    visible_lists = [session.get(GiftList, lk.list_id) for lk in links if session.get(GiftList, lk.list_id)]

    # items per list with visibility rule (same as before)
    items_for_list = {}
    for gl in visible_lists:
        if gl.owner_id == user.id:
            q = select(Item).where(Item.list_id == gl.id, Item.added_by_id == user.id, Item.owner_hidden == False)  # noqa: E712
        else:
            q = select(Item).where(Item.list_id == gl.id)
        items_for_list[gl.id] = session.exec(q).all()

    # members (approved only)
    approved_members = session.exec(
        select(Membership).where(Membership.group_id == g.id, Membership.is_approved == True)
    ).all()  # noqa: E712
    member_users = [session.get(User, mm.user_id) for mm in approved_members]

    # map member -> their list in this group (first visible list they own)
    user_list_map = {}
    for gl in visible_lists:
        user_list_map.setdefault(gl.owner_id, gl)

    return templates.TemplateResponse(
        "group_view.html",
        {
            "request": request,
            "me": user,
            "group": g,
            "visible_lists": visible_lists,
            "items_for_list": items_for_list,
            "members": member_users,
            "user_list_map": user_list_map,   # owner_id -> GiftList
            "info": info,
        },
    )
