from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session, select

from app.deps import get_session, get_current_user
from app.models import Claim, Item, GiftList, Membership, User

router = APIRouter(prefix="/claims", tags=["claims"])


@router.post("/{group_id}/claim/{item_id}")
def claim_item(
    group_id: int,
    item_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """
    Claim an item in a group. Called from the HTML group view via a small icon button.
    """
    item = session.get(Item, item_id)
    if not item:
        # If the item vanished, just go back gracefully.
        return RedirectResponse(url=f"/groups/{group_id}?error=Item+not+found", status_code=303)

    gl = session.get(GiftList, item.list_id)
    if gl.owner_id == user.id:
        # Cannot claim your own item
        return RedirectResponse(url=f"/groups/{group_id}?error=You+cannot+claim+your+own+item", status_code=303)

    # Ensure user is in the group
    mem = session.exec(
        select(Membership).where(Membership.group_id == group_id, Membership.user_id == user.id)
    ).first()
    if not mem:
        return RedirectResponse(url="/groups?error=You+are+not+a+member+of+that+group", status_code=303)

    # Check if item already claimed in this group
    existing = session.exec(
        select(Claim).where(Claim.item_id == item_id, Claim.group_id == group_id)
    ).first()
    if existing:
        if existing.claimer_id == user.id:
            # Already claimed by you; nothing to do.
            return RedirectResponse(url=f"/groups/{group_id}?info=You+are+already+getting+that+item", status_code=303)
        else:
            return RedirectResponse(url=f"/groups/{group_id}?info=That+item+is+already+being+gifted", status_code=303)

    session.add(Claim(item_id=item_id, group_id=group_id, claimer_id=user.id))
    session.commit()

    return RedirectResponse(url=f"/groups/{group_id}?info=Item+checked", status_code=303)


@router.post("/{group_id}/unclaim/{item_id}")
def unclaim_item(
    group_id: int,
    item_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """
    Remove the current user's claim on an item in this group.
    """
    existing = session.exec(
        select(Claim).where(Claim.item_id == item_id, Claim.group_id == group_id, Claim.claimer_id == user.id)
    ).first()
    if not existing:
        # Nothing to unclaim; just go back.
        return RedirectResponse(url=f"/groups/{group_id}?info=No+claim+to+remove", status_code=303)

    session.delete(existing)
    session.commit()
    return RedirectResponse(url=f"/groups/{group_id}?info=Item+unchecked", status_code=303)
