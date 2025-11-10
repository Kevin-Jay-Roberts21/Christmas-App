from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select
from app.deps import get_session, get_current_user
from app.models import GiftList, Item, User, Membership, ListGroup

router = APIRouter(prefix="/lists", tags=["lists"])
templates = Jinja2Templates(directory="app/templates")

def redirect_get(url: str) -> RedirectResponse:
    return RedirectResponse(url=url, status_code=303)

@router.get("/new")
def list_new_form(request: Request, user: User = Depends(get_current_user)):
    return templates.TemplateResponse("list_new.html", {"request": request, "me": user})

@router.get("/{list_id}")
def list_view(
    list_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    gl = session.get(GiftList, list_id)
    if not gl:
        raise HTTPException(404, "List not found")

    if gl.owner_id == user.id:
        # Owner: either show a read-only view...
        items = session.exec(
            select(Item).where(
                Item.list_id == list_id,
                Item.added_by_id == user.id,
                Item.owner_hidden == False,  # noqa: E712
            )
        ).all()
        return templates.TemplateResponse(
            "list_view.html",
            {"request": request, "me": user, "list": gl, "items": items, "is_owner": True},
        )
        # ...OR, if you’d rather always manage:
        # return RedirectResponse(url=f"/lists/{list_id}/edit", status_code=303)

    # Not owner: must be in a group where this list is visible
    visible_group_ids = session.exec(
        select(ListGroup.group_id).where(ListGroup.list_id == gl.id)
    ).all()
    if not visible_group_ids:
        raise HTTPException(403, "This list is not visible in any of your groups")

    is_member_some_visible_group = session.exec(
        select(Membership).where(
            Membership.user_id == user.id,
            Membership.group_id.in_(visible_group_ids),
        )
    ).first()

    if not is_member_some_visible_group:
        raise HTTPException(403, "You don’t have access to this list")

    # Others see all items
    items = session.exec(select(Item).where(Item.list_id == list_id)).all()
    return templates.TemplateResponse(
        "list_view.html",
        {"request": request, "me": user, "list": gl, "items": items, "is_owner": False},
    )

@router.post("/new")
def list_new(
    name: str = Form(...),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    gl = GiftList(owner_id=user.id, name=name)
    session.add(gl)
    session.commit()
    session.refresh(gl)
    # After creating, go manage items on this list
    return redirect_get(f"/lists/{gl.id}/edit")

# ----- Owner manage view (add/delete) -----

@router.get("/{list_id}/edit")
def list_edit(
    list_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    gl = session.get(GiftList, list_id)
    if not gl or gl.owner_id != user.id:
        raise HTTPException(404, "List not found")

    # Owner should only see items not hidden for them AND that they created
    items = session.exec(
        select(Item).where(
            Item.list_id == list_id,
            Item.added_by_id == user.id,
            Item.owner_hidden == False,  # noqa: E712
        )
    ).all()

    return templates.TemplateResponse(
        "list_edit.html",
        {"request": request, "me": user, "list": gl, "items": items},
    )

@router.post("/{list_id}/item")
def add_item(
    list_id: int,
    name: str = Form(...),
    url: str = Form(None),
    notes: str = Form(None),
    is_present: bool = Form(False),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    gl = session.get(GiftList, list_id)
    if not gl or gl.owner_id != user.id:
        raise HTTPException(404, "List not found")
    it = Item(
        list_id=gl.id,
        name=name,
        url=url or None,
        notes=notes or None,
        is_present=bool(is_present),
        added_by_id=user.id,
        owner_hidden=False,
    )
    session.add(it)
    session.commit()
    return redirect_get(f"/lists/{list_id}/edit")

@router.post("/{list_id}/item/{item_id}/delete")
def delete_item(
    list_id: int,
    item_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    gl = session.get(GiftList, list_id)
    if not gl or gl.owner_id != user.id:
        raise HTTPException(404, "List not found")

    it = session.get(Item, item_id)
    if not it or it.list_id != list_id:
        raise HTTPException(404, "Item not found")

    # If the owner deletes their own item, hide it from the owner,
    # but let others still see it (🦌 rule). If the owner deletes an
    # item added by others, just keep it hidden from the owner by design.
    it.owner_hidden = True
    session.add(it)
    session.commit()
    return redirect_get(f"/lists/{list_id}/edit")
