from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.considering import owned_outfit
from app.models.item import ClothingItem
from app.models.outfit import (
    FamilyOutfitRating,
    Outfit,
    OutfitItem,
    OutfitSource,
    OutfitStatus,
    UserFeedback,
)
from app.models.user import User


@dataclass
class OutfitListFilters:
    user_id: UUID
    group: bool = False
    worn: bool | None = None
    ready_to_wear: bool = False
    owned_only: bool = False
    status_filter: str | None = None
    occasion: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    source: str | None = None
    is_lookbook: bool | None = None
    is_replacement: bool | None = None
    has_source_item: bool | None = None
    item_type: str | None = None
    family_member_view: bool = False
    search: str | None = None
    occasions: list[str] | None = None
    cloned_from_outfit_id: UUID | None = None


def _escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@dataclass
class OutfitGroupRecord:
    id: UUID
    user_id: UUID
    source: OutfitSource
    status: OutfitStatus
    occasion: str
    scheduled_for: date | None
    cloned_from_outfit_id: UUID | None
    created_at: datetime
    worn: bool
    item_ids: frozenset[UUID]


@dataclass
class OutfitGroup:
    id: UUID
    hidden_member_ids: list[UUID]
    wear_count: int


def _is_template(outfit: OutfitGroupRecord) -> bool:
    return outfit.source == OutfitSource.manual and outfit.scheduled_for is None


def _is_wear(outfit: OutfitGroupRecord) -> bool:
    return (
        outfit.scheduled_for is not None
        and outfit.worn
        and outfit.cloned_from_outfit_id is not None
    )


def _saved_families(ordered, groups, hidden):
    by_id = {outfit.id: outfit for outfit in ordered}
    children = {}
    for outfit in ordered:
        children.setdefault(outfit.cloned_from_outfit_id, []).append(outfit)
    templates = {outfit.id: outfit for outfit in ordered if _is_template(outfit)}
    for template_id in templates:
        wears = [child for child in children.get(template_id, []) if _is_wear(child)]
        groups[template_id].extend(wears)
        hidden.update(wear.id for wear in wears)
    for parent_id, siblings in children.items():
        parent = by_id.get(parent_id)
        candidates = [child for child in siblings if child.id in templates]
        if not parent or not candidates or parent.id in templates or _is_wear(parent):
            continue
        owner = min(
            candidates,
            key=lambda template: (
                len(groups[template.id]) == 1,
                template.created_at,
                str(template.id),
            ),
        )
        groups[owner.id].append(parent)
        hidden.add(parent.id)


def _ai_sets(ordered, groups, hidden):
    matching = {}
    for outfit in ordered:
        if outfit.id not in hidden and outfit.source == OutfitSource.on_demand and outfit.item_ids:
            key = (outfit.user_id, outfit.occasion, outfit.item_ids)
            matching.setdefault(key, []).append(outfit)
    for members in matching.values():
        representative = min(
            members,
            key=lambda outfit: (
                0 if outfit.worn else 1 if outfit.status == OutfitStatus.accepted else 2,
                -outfit.created_at.timestamp(),
                str(outfit.id),
            ),
        )
        groups[representative.id] = members
        hidden.update(member.id for member in members if member.id != representative.id)


def _visible_groups(ordered, groups, hidden) -> list[OutfitGroup]:
    return [
        OutfitGroup(
            id=outfit.id,
            hidden_member_ids=[member.id for member in groups[outfit.id] if member.id != outfit.id],
            wear_count=sum(
                member.scheduled_for is not None and member.worn for member in groups[outfit.id]
            ),
        )
        for outfit in ordered
        if outfit.id not in hidden
    ]


def group_outfits(outfits: list[OutfitGroupRecord]) -> list[OutfitGroup]:
    """Partition filtered records into cards, with each hidden id owned by exactly one card.

    Wears belong to their direct template; shared parents prefer worn, then oldest templates.
    Remaining AI sets prefer the newest worn, then accepted, then other record, with id ties.
    """
    ordered = sorted(outfits, key=lambda outfit: str(outfit.id))
    ordered.sort(key=lambda outfit: outfit.created_at, reverse=True)
    groups = {outfit.id: [outfit] for outfit in ordered}
    hidden = set()
    _saved_families(ordered, groups, hidden)
    _ai_sets(ordered, groups, hidden)
    return _visible_groups(ordered, groups, hidden)


class OutfitService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def set_status(self, outfit_id: UUID, user_id: UUID, new_status: OutfitStatus) -> Outfit:
        result = await self.db.execute(
            select(Outfit).where(and_(Outfit.id == outfit_id, Outfit.user_id == user_id))
        )
        outfit = result.scalar_one_or_none()

        if not outfit:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"message": "Outfit not found", "error_code": "OUTFIT_NOT_FOUND"},
            )

        outfit.status = new_status
        outfit.responded_at = datetime.utcnow()
        await self.db.commit()

        refreshed = await self.db.execute(
            select(Outfit)
            .where(Outfit.id == outfit_id)
            .options(
                selectinload(Outfit.items).selectinload(OutfitItem.item),
                selectinload(Outfit.feedback),
                selectinload(Outfit.family_ratings).selectinload(FamilyOutfitRating.user),
            )
        )
        return refreshed.scalar_one()

    def _build_filter_clauses(self, filters: OutfitListFilters) -> list:
        clauses = [Outfit.user_id == filters.user_id]

        if filters.worn is not None:
            predicate = Outfit.feedback.has(UserFeedback.worn_at.is_not(None))
            clauses.append(predicate if filters.worn else ~predicate)
        if filters.status_filter and "pending" in [
            part.strip() for part in filters.status_filter.split(",")
        ]:
            clauses.append(~func.coalesce(Outfit.ai_raw_response.has_key("inspiration"), False))

        if (
            filters.ready_to_wear
            or filters.owned_only
            or filters.date_from is not None
            or filters.date_to is not None
            or filters.has_source_item is True
        ):
            clauses.append(owned_outfit())

        if filters.ready_to_wear:
            clauses.append(
                ~Outfit.items.any(OutfitItem.item.has(ClothingItem.needs_wash.is_(True)))
            )

        if filters.family_member_view:
            clauses.append(Outfit.scheduled_for.is_not(None))

        if filters.status_filter:
            parsed_statuses: list[OutfitStatus] = []
            for s in filters.status_filter.split(","):
                try:
                    parsed_statuses.append(OutfitStatus(s.strip()))
                except ValueError:
                    continue
            if len(parsed_statuses) == 1:
                clauses.append(Outfit.status == parsed_statuses[0])
            elif parsed_statuses:
                clauses.append(Outfit.status.in_(parsed_statuses))

        if filters.occasion:
            clauses.append(Outfit.occasion == filters.occasion)

        if filters.date_from:
            clauses.append(Outfit.scheduled_for >= filters.date_from)

        if filters.date_to:
            clauses.append(Outfit.scheduled_for <= filters.date_to)

        if filters.source:
            source_values: list[OutfitSource] = []
            for s in filters.source.split(","):
                try:
                    source_values.append(OutfitSource(s.strip()))
                except ValueError:
                    continue
            if OutfitSource.pairing in source_values:
                clauses.append(owned_outfit())
            if len(source_values) == 1:
                clauses.append(Outfit.source == source_values[0])
            elif source_values:
                clauses.append(Outfit.source.in_(source_values))

        if filters.is_lookbook is True:
            clauses.append(Outfit.scheduled_for.is_(None))
        elif filters.is_lookbook is False:
            clauses.append(Outfit.scheduled_for.is_not(None))

        if filters.is_replacement is True:
            clauses.append(Outfit.replaces_outfit_id.is_not(None))
        elif filters.is_replacement is False:
            clauses.append(Outfit.replaces_outfit_id.is_(None))

        if filters.has_source_item is True:
            clauses.append(Outfit.source_item_id.is_not(None))
        elif filters.has_source_item is False:
            clauses.append(Outfit.source_item_id.is_(None))

        if filters.item_type:
            clauses.append(Outfit.source_item.has(ClothingItem.type == filters.item_type))

        # Occasions match too because unnamed outfits are titled by their translated occasion;
        # the client sends the occasion keys whose label contains the search term.
        search_matches = []
        if filters.search:
            search_matches.append(
                Outfit.name.ilike(f"%{_escape_like(filters.search)}%", escape="\\")
            )
        if filters.occasions:
            search_matches.append(Outfit.occasion.in_(filters.occasions))
        if search_matches:
            clauses.append(or_(*search_matches))

        if filters.cloned_from_outfit_id is not None:
            clauses.append(Outfit.cloned_from_outfit_id == filters.cloned_from_outfit_id)

        return clauses

    async def get_ids_by_filter(
        self,
        filters: OutfitListFilters,
        excluded_ids: list[UUID] | None = None,
        after_id: UUID | None = None,
        limit: int | None = None,
    ) -> list[UUID]:
        clauses = self._build_filter_clauses(filters)
        if excluded_ids:
            clauses.append(Outfit.id.notin_(excluded_ids))
        if after_id is not None:
            clauses.append(Outfit.id > after_id)

        # invariant: a capped bulk delete resumes with after_id, so the order
        # has to be total and stable across requests.
        query = select(Outfit.id).where(and_(*clauses)).order_by(Outfit.id.asc())
        if limit is not None:
            query = query.limit(limit)

        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def list_with_filters(
        self, filters: OutfitListFilters, page: int, page_size: int
    ) -> tuple[list[Outfit], int]:
        clauses = self._build_filter_clauses(filters)

        if filters.group:
            result = await self.db.execute(
                select(
                    Outfit.id,
                    Outfit.user_id,
                    Outfit.source,
                    Outfit.status,
                    Outfit.occasion,
                    Outfit.scheduled_for,
                    Outfit.cloned_from_outfit_id,
                    Outfit.created_at,
                    UserFeedback.worn_at.is_not(None).label("worn"),
                    func.array_agg(OutfitItem.item_id)
                    .filter(OutfitItem.item_id.is_not(None))
                    .label("item_ids"),
                )
                .outerjoin(UserFeedback, UserFeedback.outfit_id == Outfit.id)
                .outerjoin(OutfitItem, OutfitItem.outfit_id == Outfit.id)
                .where(and_(*clauses))
                .group_by(Outfit.id, UserFeedback.id)
            )
            records = []
            for row in result:
                values = dict(row._mapping)
                values["item_ids"] = frozenset(values["item_ids"] or [])
                records.append(OutfitGroupRecord(**values))
            grouped = group_outfits(records)
            start = (page - 1) * page_size
            page_groups = grouped[start : start + page_size]
            if not page_groups:
                return [], len(grouped)
            hydrated = await self.db.execute(
                select(Outfit)
                .where(Outfit.id.in_([group.id for group in page_groups]))
                .options(
                    selectinload(Outfit.items).selectinload(OutfitItem.item),
                    selectinload(Outfit.feedback),
                    selectinload(Outfit.family_ratings).selectinload(FamilyOutfitRating.user),
                )
            )
            by_id = {outfit.id: outfit for outfit in hydrated.scalars().all()}
            outfits = []
            for group in page_groups:
                outfit = by_id[group.id]
                outfit.group_size = 1 + len(group.hidden_member_ids)
                outfit.hidden_member_ids = group.hidden_member_ids
                outfit.wear_count = group.wear_count
                outfits.append(outfit)
            return outfits, len(grouped)

        count_query: Select = select(func.count()).select_from(Outfit).where(and_(*clauses))
        total = (await self.db.execute(count_query)).scalar_one()

        query = (
            select(Outfit)
            .where(and_(*clauses))
            .options(
                selectinload(Outfit.items).selectinload(OutfitItem.item),
                selectinload(Outfit.feedback),
                selectinload(Outfit.family_ratings).selectinload(FamilyOutfitRating.user),
            )
            .order_by(Outfit.created_at.desc(), Outfit.id.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )

        result = await self.db.execute(query)
        outfits = list(result.scalars().all())
        return outfits, total

    async def verify_family_access(self, current_user: User, family_member_id: UUID) -> UUID:
        if not current_user.family_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "message": "You must be in a family to view family member outfits",
                    "error_code": "NOT_IN_FAMILY",
                },
            )
        member_result = await self.db.execute(
            select(User).where(User.id == family_member_id, User.is_active == True)  # noqa: E712
        )
        member = member_result.scalar_one_or_none()
        if not member or member.family_id != current_user.family_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User is not in your family",
            )
        return family_member_id
