from datetime import datetime
from typing import Optional, List
from sqlmodel import SQLModel, Field, Relationship, UniqueConstraint

RECENT_WINDOW_MIN = 60 * 24  # 24h

class User(SQLModel, table=True):
    __tablename__ = "user"
    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(index=True, unique=True)
    username: str = Field(index=True, unique=True)
    password_hash: str
    created_at: datetime = Field(default_factory=datetime.utcnow)

    lists: List["GiftList"] = Relationship(back_populates="owner")
    memberships: List["Membership"] = Relationship(back_populates="user")

class GiftList(SQLModel, table=True):
    __tablename__ = "giftlist"
    id: Optional[int] = Field(default=None, primary_key=True)
    owner_id: int = Field(foreign_key="user.id", index=True)
    name: str
    created_at: datetime = Field(default_factory=datetime.utcnow)

    owner: User = Relationship(back_populates="lists")
    items: List["Item"] = Relationship(back_populates="gift_list")

class Item(SQLModel, table=True):
    __tablename__ = "item"
    id: Optional[int] = Field(default=None, primary_key=True)
    list_id: int = Field(foreign_key="giftlist.id", index=True)

    # Content
    name: str
    url: Optional[str] = None
    notes: Optional[str] = None

    # Who created the item (on whose behalf)
    added_by_id: int = Field(index=True)  # user who added the item

    # Priority icon: 🎁
    is_present: bool = Field(default=False)  # "I REALLY want this"

    # Visibility rule for list owner (soft-hide):
    # - If True, the list owner won't see this item in their own view.
    # - Others in the group still can (with 🦌 indicator).
    owner_hidden: bool = Field(default=False)

    created_at: datetime = Field(default_factory=datetime.utcnow)

    gift_list: GiftList = Relationship(back_populates="items")
    claims: List["Claim"] = Relationship(back_populates="item")


class Group(SQLModel, table=True):
    __tablename__ = "group"
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    leader_id: int = Field(foreign_key="user.id", index=True)  # NEW
    created_at: datetime = Field(default_factory=datetime.utcnow)

    memberships: List["Membership"] = Relationship(back_populates="group")
    visible_lists: List["ListGroup"] = Relationship(back_populates="group")

class Membership(SQLModel, table=True):
    __tablename__ = "membership"
    id: Optional[int] = Field(default=None, primary_key=True)
    group_id: int = Field(foreign_key="group.id", index=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    selected_list_id: Optional[int] = Field(foreign_key="giftlist.id", default=None)

    is_approved: bool = Field(default=False)  # NEW

    group: Group = Relationship(back_populates="memberships")
    user: "User" = Relationship(back_populates="memberships")

    __table_args__ = (
        UniqueConstraint("group_id", "user_id", name="uq_membership_group_user"),
    )

class ListGroup(SQLModel, table=True):
    __tablename__ = "listgroup"
    id: Optional[int] = Field(default=None, primary_key=True)
    group_id: int = Field(foreign_key="group.id", index=True)
    list_id: int = Field(foreign_key="giftlist.id", index=True)

    group: Group = Relationship(back_populates="visible_lists")

    __table_args__ = (
        UniqueConstraint("group_id", "list_id", name="uq_list_in_group"),
    )

class Claim(SQLModel, table=True):
    __tablename__ = "claim"
    id: Optional[int] = Field(default=None, primary_key=True)
    item_id: int = Field(foreign_key="item.id", index=True)
    group_id: int = Field(foreign_key="group.id", index=True)
    claimer_id: int = Field(foreign_key="user.id", index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    item: Item = Relationship(back_populates="claims")

    __table_args__ = (
        UniqueConstraint("item_id", "group_id", name="uq_claim_item_group"),
    )
