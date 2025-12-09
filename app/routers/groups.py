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
    error = request.query_params.get("error")
    info = request.query_params.get("info")
    return templates.TemplateResponse(
        "group_new.html",
        {
            "request": request,
            "me": user,
            "my_lists": my_lists,
            "error": error,
            "info": info,
        },
    )

@router.post("/new")
def group_new(
    name: str = Form(...),
    selected_list_id: int = Form(...),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    name = name.strip()

    # case-insensitive check for an existing group with this name
    existing = session.exec(
        select(Group).where(Group.name.ilike(name))
    ).first()
    if existing:
        return redirect_get(
            "/groups/new",
            {"error": f"A group named '{name}' already exists."},
        )

    gl = session.get(GiftList, selected_list_id)
    if not gl or gl.owner_id != user.id:
        return redirect_get(
            "/groups/new",
            {"error": "Invalid list selection."},
        )

    g = Group(name=name, leader_id=user.id)  # leader = creator
    session.add(g)
    session.commit()
    session.refresh(g)

    # leader auto-membership approved
    session.add(
        Membership(
            group_id=g.id,
            user_id=user.id,
            selected_list_id=gl.id,
            is_approved=True,
        )
    )

    # show leader's list
    if not session.exec(
        select(ListGroup).where(ListGroup.group_id == g.id, ListGroup.list_id == gl.id)
    ).first():
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
        # Already a member
        if mem.is_approved and not mem.is_denied:
            return redirect_get(
                "/groups",
                {"info": "You are already in this group."},
            )

        # Already have a pending join request
        if not mem.is_approved and not mem.is_denied and not mem.is_invite:
            return redirect_get(
                "/groups/join",
                {"info": "You have already requested to join this group. Please wait for the leader to approve."},
            )

        # Already have an invitation
        if mem.is_invite and not mem.is_approved and not mem.is_denied:
            return redirect_get(
                "/groups",
                {"info": "You already have an invitation to this group. Check the 'Invitations for you' section."},
            )

        # Otherwise (e.g. previously denied) → turn it into a fresh join request
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

    def user_of(mem):
        return session.get(User, mem.user_id)

    def list_of(mem):
        return session.get(GiftList, mem.selected_list_id) if mem.selected_list_id else None

    error = request.query_params.get("error")
    info = request.query_params.get("info")

    return templates.TemplateResponse(
        "group_manage.html",
        {
            "request": request,
            "me": user,
            "group": g,
            "pending": [(mem, user_of(mem), list_of(mem)) for mem in pending],
            "approved": [(mem, user_of(mem), list_of(mem)) for mem in approved],
            "invited": [(mem, user_of(mem), list_of(mem)) for mem in invited],
            "error": error,
            "info": info,
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

@router.post("/{group_id}/kick/{user_id}")
def kick_member(
    group_id: int,
    user_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    # Only the leader can remove members
    g = session.get(Group, group_id)
    if not g or g.leader_id != user.id:
        raise HTTPException(403, "Leader only")

    # Find this user's membership in the group
    mem = session.exec(
        select(Membership).where(
            Membership.group_id == g.id,
            Membership.user_id == user_id,
        )
    ).first()
    if not mem:
        raise HTTPException(404, "Membership not found")

    # Remove their membership
    session.delete(mem)

    # Remove ANY of this user's lists that are linked to this group
    user_lists = session.exec(
        select(GiftList).where(GiftList.owner_id == user_id)
    ).all()
    for gl in user_lists:
        # Remove the list-group link (so their list is no longer shown in this group)
        lg = session.exec(
            select(ListGroup).where(
                ListGroup.group_id == g.id,
                ListGroup.list_id == gl.id,
            )
        ).first()
        if lg:
            session.delete(lg)

        # Delete surprise gifts for this user that belong to THIS group:
        # - items on their list
        # - hidden from them
        # - added by someone else
        surprise_items = session.exec(
            select(Item).where(
                Item.list_id == gl.id,
                Item.owner_hidden == True,
                Item.added_by_id != gl.owner_id,
            )
        ).all()
        for it in surprise_items:
            # Only consider those that actually have claims in this group
            it_claims = session.exec(
                select(Claim).where(
                    Claim.item_id == it.id,
                    Claim.group_id == g.id,
                )
            ).all()
            if it_claims:
                for c in it_claims:
                    session.delete(c)
                session.delete(it)

    # Remove any claims they made in this group
    claims = session.exec(
        select(Claim).where(
            Claim.group_id == g.id,
            Claim.claimer_id == user_id,
        )
    ).all()
    for c in claims:
        session.delete(c)

    session.commit()
    return redirect_get(f"/groups/{g.id}/manage")

@router.post("/{group_id}/leave")
def leave_group(
    group_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    g = session.get(Group, group_id)
    if not g:
        raise HTTPException(404, "Group not found")

    # Leader can't "leave" their own group — they should delete it instead
    if g.leader_id == user.id:
        raise HTTPException(400, "Leader must delete the group instead of leaving it")

    # Find *this* user's membership
    mem = session.exec(
        select(Membership).where(
            Membership.group_id == g.id,
            Membership.user_id == user.id,
        )
    ).first()
    if not mem:
        raise HTTPException(404, "You are not a member of this group")

    # Remove membership
    session.delete(mem)

    # Remove ANY of this user's lists that are linked to this group
    user_lists = session.exec(
        select(GiftList).where(GiftList.owner_id == user.id)
    ).all()
    for gl in user_lists:
        # Remove list-group link
        lg = session.exec(
            select(ListGroup).where(
                ListGroup.group_id == g.id,
                ListGroup.list_id == gl.id,
            )
        ).first()
        if lg:
            session.delete(lg)

        # Delete surprise gifts for this user that belong to THIS group
        surprise_items = session.exec(
            select(Item).where(
                Item.list_id == gl.id,
                Item.owner_hidden == True,
                Item.added_by_id != gl.owner_id,
            )
        ).all()
        for it in surprise_items:
            it_claims = session.exec(
                select(Claim).where(
                    Claim.item_id == it.id,
                    Claim.group_id == g.id,
                )
            ).all()
            if it_claims:
                for c in it_claims:
                    session.delete(c)
                session.delete(it)

    # Remove any claims they made in this group
    claims = session.exec(
        select(Claim).where(
            Claim.group_id == g.id,
            Claim.claimer_id == user.id,
        )
    ).all()
    for c in claims:
        session.delete(c)

    session.commit()
    return redirect_get("/groups")

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

    # Normalize input
    ident_raw = invitee.strip()
    ident_email = ident_raw.lower()

    # find user by username OR email (email is case-insensitive)
    target = session.exec(
        select(User).where(
            or_(User.username == ident_raw, User.email == ident_email)
        )
    ).first()

    if not target:
        # customized error text
        return redirect_get(
            f"/groups/{g.id}/manage",
            {"error": f"Could not find user '{invitee}'."},
        )

    if target.id == user.id:
        return redirect_get(
            f"/groups/{g.id}/manage",
            {"error": "You can’t invite yourself."},
        )

    # existing membership?
    mem = session.exec(
        select(Membership).where(
            Membership.group_id == g.id,
            Membership.user_id == target.id,
        )
    ).first()

    # already in the group
    if mem and mem.is_approved and not mem.is_denied:
        return redirect_get(
            f"/groups/{g.id}/manage",
            {"info": "That user is already in the group."},
        )

    # already has a pending invite → don’t send another
    if mem and mem.is_invite and not mem.is_approved and not mem.is_denied:
        return redirect_get(
            f"/groups/{g.id}/manage",
            {"info": f"{target.username} has already been invited and hasn’t responded yet."},
        )

    if mem:
        # Some other prior state (e.g. they requested to join, or previously denied)
        mem.is_approved = False
        mem.is_denied = False
        mem.is_invite = True
        # keep selected_list_id as-is (usually None for a pure invite)
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
    return redirect_get(
        f"/groups/{g.id}/manage",
        {"info": f"Invitation sent to {target.username}."},
    )


@router.post("/{group_id}/delete")
def delete_group(
    group_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    g = session.get(Group, group_id)
    if not g:
        raise HTTPException(404, "Group not found")

    if g.leader_id != user.id:
        raise HTTPException(403, "Only the leader can delete this group")

    # Delete all memberships for this group
    memberships = session.exec(
        select(Membership).where(Membership.group_id == g.id)
    ).all()
    for mem in memberships:
        session.delete(mem)

    # Delete all visible-list links for this group
    links = session.exec(
        select(ListGroup).where(ListGroup.group_id == g.id)
    ).all()
    for lk in links:
        session.delete(lk)

    # Delete all claims in this group
    claims = session.exec(
        select(Claim).where(Claim.group_id == g.id)
    ).all()
    for c in claims:
        session.delete(c)

    # Finally delete the group itself
    session.delete(g)
    session.commit()

    # Everyone is now “not part of the group” anymore
    return redirect_get("/groups")


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
            # Owner: only see your own non-hidden items (no surprises)
            q = select(Item).where(
                Item.list_id == gl.id,
                Item.added_by_id == user.id,
                Item.owner_hidden == False,  # noqa: E712
            )
        else:
            # Non-owner: see global items (group_id is NULL) + any surprise
            # items that belong to *this* group.
            q = select(Item).where(
                Item.list_id == gl.id,
                or_(Item.group_id == None, Item.group_id == g.id),  # noqa: E711
            )
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

        # Claims in THIS group (for "You are getting this" + unclaim button)
    group_claims = session.exec(
        select(Claim).where(Claim.group_id == g.id)
    ).all()
    my_group_claimed_item_ids = {
        c.item_id for c in group_claims if c.claimer_id == user.id
    }

    # Claims in ANY group for the items shown here (for global "already gifted" state)
    all_item_ids = {it.id for items in items_for_list.values() for it in items}
    if all_item_ids:
        all_claims = session.exec(
            select(Claim).where(Claim.item_id.in_(all_item_ids))
        ).all()
        # Items YOU have claimed anywhere
        my_any_claimed_item_ids = {
            c.item_id for c in all_claims if c.claimer_id == user.id
        }
        # Items that are claimed by someone (you or others)
        claimed_item_ids = {c.item_id for c in all_claims}
    else:
        my_any_claimed_item_ids = set()
        claimed_item_ids = set()

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
            "my_group_claimed_item_ids": my_group_claimed_item_ids,
            "my_any_claimed_item_ids": my_any_claimed_item_ids,
            "info": info,
        },
    )


@router.post("/{group_id}/surprise/{list_id}")
def add_surprise_item(
    group_id: int,
    list_id: int,
    name: str = Form(...),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    g = session.get(Group, group_id)
    if not g:
        raise HTTPException(404, "Group not found")

    # must be leader or approved member (same rule as group_view)
    mem = session.exec(
        select(Membership).where(Membership.group_id == g.id, Membership.user_id == user.id)
    ).first()
    if not (user.id == g.leader_id or (mem and mem.is_approved)):
        raise HTTPException(403, "Not approved to add gifts in this group")

    gl = session.get(GiftList, list_id)
    if not gl:
        raise HTTPException(404, "List not found")

    # ensure this list is actually shared in this group
    link = session.exec(
        select(ListGroup).where(ListGroup.group_id == g.id, ListGroup.list_id == gl.id)
    ).first()
    if not link:
        raise HTTPException(403, "That list is not shared in this group")

    # you shouldn't add a surprise item to your own list
    if gl.owner_id == user.id:
        raise HTTPException(400, "You cannot add a surprise item to your own list")

    gift_name = (name or "").strip()
    if not gift_name:
        return redirect_get(f"/groups/{group_id}", {"info": "Please provide a name for the gift."})

    # Create hidden item on their list.
    # - owner_hidden=True so the list owner never sees it.
    # - added_by_id = you (the giver).
    it = Item(
        list_id=gl.id,
        group_id=g.id,         # surprise gift is *scoped* to this group
        name=gift_name,
        url=None,
        notes=None,
        is_present=False,      # no 🎁 icon for these; per your request
        added_by_id=user.id,
        owner_hidden=True,
    )
    session.add(it)
    session.commit()  # so it.id is available

    # Automatically claim it for this user in this group
    existing_claim = session.exec(
        select(Claim).where(Claim.item_id == it.id, Claim.group_id == g.id)
    ).first()
    if not existing_claim:
        claim = Claim(item_id=it.id, group_id=g.id, claimer_id=user.id)
        session.add(claim)
        session.commit()

    owner = session.get(User, gl.owner_id)
    owner_name = owner.username if owner else "this person"

    return redirect_get(
        f"/groups/{group_id}",
        {"info": f"Added a surprise gift for {owner_name}."},
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

    mem = session.exec(
        select(Membership).where(
            Membership.group_id == g.id,
            Membership.user_id == user.id
        )
    ).first()
    if not mem:
        raise HTTPException(404, "Invite not found")
    if mem.is_approved:
        return redirect_get("/groups", {"info": "Already a member."})

    gl = session.get(GiftList, selected_list_id)
    if not gl or gl.owner_id != user.id:
        return redirect_get("/groups", {"error": "Please select one of your lists."})

    mem.selected_list_id = gl.id
    mem.is_approved = True
    mem.is_invite = False      # <-- important line
    mem.is_denied = False

    if not session.exec(
        select(ListGroup).where(
            ListGroup.group_id == g.id,
            ListGroup.list_id == gl.id
        )
    ).first():
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
