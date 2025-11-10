from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select
from app.deps import get_session, get_current_user
from app.models import Claim, Item, GiftList, Membership, User

router = APIRouter(prefix="/claims", tags=["claims"])

@router.post("/{group_id}/claim/{item_id}")
def claim_item(
    group_id: int,
    item_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    item = session.get(Item, item_id)
    if not item:
        raise HTTPException(404, "Item not found")
    gl = session.get(GiftList, item.list_id)
    if gl.owner_id == user.id:
        raise HTTPException(403, "Cannot claim your own item")
    mem = session.exec(select(Membership).where(Membership.group_id == group_id, Membership.user_id == user.id)).first()
    if not mem:
        raise HTTPException(403, "Not in group")
    # Insert claim (unique on item_id, group_id)
    if session.exec(select(Claim).where(Claim.item_id == item_id, Claim.group_id == group_id)).first():
        raise HTTPException(409, "Item already claimed")
    session.add(Claim(item_id=item_id, group_id=group_id, claimer_id=user.id))
    session.commit()
    return {"status": "claimed"}

@router.post("/{group_id}/unclaim/{item_id}")
def unclaim_item(
    group_id: int,
    item_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    existing = session.exec(
        select(Claim).where(Claim.item_id == item_id, Claim.group_id == group_id, Claim.claimer_id == user.id)
    ).first()
    if not existing:
        raise HTTPException(404, "No claim by you on this item")
    session.delete(existing)
    session.commit()
    return {"status": "unclaimed"}
