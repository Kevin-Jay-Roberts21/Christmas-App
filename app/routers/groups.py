from typing import Optional, Dict
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select, or_
from app.deps import get_session, get_current_user
from app.models import Group, Membership, ListGroup, GiftList, Item, User, Claim

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
    session.commit()
    session.refresh(g)

    # leader auto-membership approved
    session.add(Membership(group_id=g.id, user_id=user.id, selected_list_id=gl.id, is_approved=True))

    # show leader's list
    if not session.exec(select(ListGroup).where(ListGroup.group_id == g.id, ListGroup.list_id == gl.id)).first():
        session.add(ListGroup(group_id=g.id, list_id=gl.id))

    session.commit()
    return redirect_get(f"/groups/{g.id}")


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
        if g:
            results = [g]
    else:
        results = session.exec(select(Group).where(Group.name.ilike(f"%{q}%"))).all()

    return templates.TemplateResponse(
        "group_search.html",
        {"request": request, "me": user, "results": results, "q": q}
    )


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
    return templates.TemplateResponse(
        "group_join.html",
        {"request": request, "me": user, "my_lists": my_lists, "error": error, "info": info}
    )


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

    mem = session.exec(
        select(Membership).where(Membership.group_id == g.id, Membership.user_id == user.id)
    ).first()

    if mem:
        # Update to a *request* state (not an invite)
        mem.selected_list_id = gl.id
        mem.is_approved = False
        mem.is_denied = False
        mem.is_invite = False
    else:
        mem = Membership(
            group_id=g.id,
            user_id=user.id,
            selected_list_id=gl.id,
            is_approved=False,
            is_denied=False,
            is_invite=False,  # request (pending)
        )
        session.add(mem)

    session.commit()
    return redirect_get("/groups/join", {"info": "Request sent to the group leader."})


# ---------- Leader manage: approve / invite --------------------------------

@router.get("/{group_id}/manage")
def manage_group(
    group_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    g = session.get(Group, group_id)
    if not g:
        raise HTTPException(404, "Group not found")
    if g.leader_id != user.id:
        raise HTTPException(403, "Only the leader can manage the group")

    # True join requests (not invitations)
    pending = session.exec(
        select(Membership).where(
            Membership.group_id == g.id,
            Membership.is_approved == False,   # noqa: E712
            Membership.is_denied == False,
            Membership.is_invite == False
        )
    ).all()

    # Invited (awaiting acceptance)
    invited = session.exec(
        select(Membership).where(
            Membership.group_id == g.id,
            Membership.is_approved == False,   # noqa: E712
            Membership.is_denied == False,
            Membership.is_invite == True
        )
    ).all()

    # Approved members
    approved = session.exec(
        select(Membership).where(
            Membership.group_id == g.id,
            Membership.is_approved == True,    # noqa: E712
            Membership.is_denied == False
        )
    ).all()

    def user_of(mem):  # helpers for template
        return session.get(User, mem.user_id)

    def list_of(mem):
        return session.get(GiftList, mem.selected_list_id) if mem.selected_list_id else None

    return templates.TemplateResponse(
        "group_manage.html",
        {
            "request": request,
            "me": user,
            "group": g,
            "pending": [(mem, user_of(mem), list_of(mem)) for mem in pending],
            "approved": [(mem, user_of(mem), list_of(mem)) for mem in approved],
            "invited": [(mem, user_of(mem), list_of(mem)) for mem in invited],
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
    if not g or g.leader_id != user.id:
        raise HTTPException(403, "Leader only")

    mem = session.get(Membership, membership_id)
    if not mem or mem.group_id != g.id:
        raise HTTPException(404, "Membership not found")

    mem.is_approved = True
    # ensure their list is visible in group
    if mem.selected_list_id and not session.exec(
        select(ListGroup).where(ListGroup.group_id == g.id, ListGroup.list_id == mem.selected_list_id)
    ).first():
        session.add(ListGroup(group_id=g.id, list_id=mem.selected_list_id))

    session.commit()
    return redirect_get(f"/groups/{g.id}/manage")


@router.post("/{group_id}/deny")
def deny_member(
    group_id: int,
    membership_id: int = Form(...),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    g = session.get(Group, group_id)
    if not g or g.leader_id != user.id:
        raise HTTPException(403, "Leader only")

    mem = session.get(Membership, membership_id)
    if not mem or mem.group_id != g.id:
        raise HTTPException(404, "Membership not found")

    mem.is_approved = False
    mem.is_denied = True
    session.commit()
    return redirect_get(f"/groups/{g.id}/manage")


@router.post("/{group_id}/invite")
def invite_member(
    group_id: int,
    invitee: str = Form(...),                        # username or email
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    g = session.get(Group, group_id)
    if not g or g.leader_id != user.id:
        raise HTTPException(status_code=403, detail="Leader only")

    # find user by username OR email
    target = session.exec(
        select(User).where(or_(User.username == invitee, User.email == invitee))
    ).first()

    if not target:
        return redirect_get(f"/groups/{g.id}/manage", {"error": "Whoops, that user does not exist."})

    if target.id == user.id:
        return redirect_get(f"/groups/{g.id}/manage", {"error": "You can’t invite yourself."})

    # existing membership?
    mem = session.exec(
        select(Membership).where(
            Membership.group_id == g.id,
            Membership.user_id == target.id,
        )
    ).first()

    # already in the group
    if mem and mem.is_approved and not mem.is_denied:
        return redirect_get(f"/groups/{g.id}/manage", {"info": "That user is already in the group."})

    if mem:
        # switch any prior state to a fresh invite
        mem.is_approved = False
        mem.is_denied = False
        mem.is_invite = True
        # keep selected_list_id as-is (usually None)
    else:
        mem = Membership(
            group_id=g.id,
            user_id=target.id,
            selected_list_id=None,
            is_approved=False,
            is_denied=False,
            is_invite=True,   # invitation
        )
        session.add(mem)

    session.commit()
    return redirect_get(f"/groups/{g.id}/manage", {"info": f"Invitation sent to {target.username}."})


# ---------- Group view -----------------------------------------------------

@router.get("/{group_id}")
def group_view(
    group_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
    info: Optional[str] = None,
):
    g = session.get(Group, group_id)
    if not g:
        raise HTTPException(404, "Group not found")

    # must be leader or approved member
    mem = session.exec(
        select(Membership).where(Membership.group_id == g.id, Membership.user_id == user.id)
    ).first()
    if not (user.id == g.leader_id or (mem and mem.is_approved)):
        raise HTTPException(403, "Not approved to view this group")

    # visible lists in group
    links = session.exec(select(ListGroup).where(ListGroup.group_id == g.id)).all()
    visible_lists = [session.get(GiftList, lk.list_id) for lk in links if session.get(GiftList, lk.list_id)]

    # items per list with visibility rule
    items_for_list = {}
    for gl in visible_lists:
        if gl.owner_id == user.id:
            q = select(Item).where(Item.list_id == gl.id, Item.added_by_id == user.id, Item.owner_hidden == False)  # noqa: E712
        else:
            q = select(Item).where(Item.list_id == gl.id)
        items_for_list[gl.id] = session.exec(q).all()

    # members (approved only)
    approved_members = session.exec(
        select(Membership).where(Membership.group_id == g.id, Membership.is_approved == True)  # noqa: E712
    ).all()
    member_users = [session.get(User, mm.user_id) for mm in approved_members]

    # owner_id -> GiftList (first visible)
    user_list_map = {}
    for gl in visible_lists:
        user_list_map.setdefault(gl.owner_id, gl)

    # Claims for this group
    claims = session.exec(select(Claim).where(Claim.group_id == g.id)).all()
    claimed_item_ids = {c.item_id for c in claims}
    my_claimed_item_ids = {c.item_id for c in claims if c.claimer_id == user.id}
    owner_map = {u.id: u for u in member_users}

    return templates.TemplateResponse(
        "group_view.html",
        {
            "request": request,
            "me": user,
            "group": g,
            "visible_lists": visible_lists,
            "items_for_list": items_for_list,
            "members": member_users,
            "user_list_map": user_list_map,
            "owner_map": owner_map,
            "claimed_item_ids": claimed_item_ids,
            "my_claimed_item_ids": my_claimed_item_ids,
            "info": info,
        },
    )


@router.post("/{group_id}/accept_invite")
def accept_invite(
    group_id: int,
    selected_list_id: int = Form(...),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    g = session.get(Group, group_id)
    if not g:
        raise HTTPException(404, "Group not found")

    mem = session.exec(select(Membership).where(Membership.group_id == g.id, Membership.user_id == user.id)).first()
    if not mem:
        raise HTTPException(404, "Invite not found")
    if mem.is_approved:
        return redirect_get("/groups", {"info": "Already a member."})

    gl = session.get(GiftList, selected_list_id)
    if not gl or gl.owner_id != user.id:
        return redirect_get("/groups", {"error": "Please select one of your lists."})

    mem.selected_list_id = gl.id
    mem.is_approved = True

    if not session.exec(select(ListGroup).where(ListGroup.group_id == g.id, ListGroup.list_id == gl.id)).first():
        session.add(ListGroup(group_id=g.id, list_id=gl.id))

    session.commit()
    return redirect_get("/groups", {"info": "Invite accepted."})


@router.post("/{group_id}/decline_invite")
def decline_invite(
    group_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    g = session.get(Group, group_id)
    if not g:
        raise HTTPException(404, "Group not found")

    mem = session.exec(select(Membership).where(Membership.group_id == g.id, Membership.user_id == user.id)).first()
    if not mem:
        return redirect_get("/groups")

    session.delete(mem)
    session.commit()
    return redirect_get("/groups", {"info": "Invite declined."})


@router.post("/{group_id}/remove_denied")
def remove_denied(
    group_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    g = session.get(Group, group_id)
    if not g:
        return redirect_get("/groups")

    mem = session.exec(
        select(Membership).where(Membership.group_id == g.id, Membership.user_id == user.id)
    ).first()
    if mem and mem.is_denied:
        session.delete(mem)
        session.commit()

    return redirect_get("/groups")
