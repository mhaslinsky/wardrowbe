from app.utils.garment_vocabulary import DEFAULT_WASH_INTERVALS, NEVER_WASHED_TYPES


def never_washed(item) -> bool:
    return (item.type or "").strip().lower() in NEVER_WASHED_TYPES


def wash_interval(item) -> int:
    if never_washed(item):
        return 999
    if item.wash_interval is not None:
        return item.wash_interval
    configured = vars(item).get("type_wash_interval")
    if configured is not None:
        return configured
    return DEFAULT_WASH_INTERVALS.get((item.type or "").lower(), 3)
