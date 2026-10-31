from sqlalchemy import func
from sqlalchemy.sql.elements import ColumnElement

from app.utils.garment_vocabulary import DEFAULT_WASH_INTERVALS, NEVER_WASHED_TYPES


def never_washed(item) -> bool | ColumnElement[bool]:
    """Return a boolean for an item or a SQL condition for its model class."""
    item_type = item.type
    if isinstance(item_type, str) or item_type is None:
        return (item_type or "").strip().lower() in NEVER_WASHED_TYPES
    return func.lower(func.btrim(item_type)).in_(NEVER_WASHED_TYPES)


def wash_interval(item) -> int:
    if never_washed(item):
        return 999
    if item.wash_interval is not None:
        return item.wash_interval
    configured = vars(item).get("type_wash_interval")
    if configured is not None:
        return configured
    return DEFAULT_WASH_INTERVALS.get((item.type or "").lower(), 3)
