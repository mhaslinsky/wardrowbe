def image_url(path: str) -> str:
    return f"/photos/images/{path}"


def optional_image_url(path: str | None, fallback: str | None = None) -> str | None:
    target = path or fallback
    return image_url(target) if target else None
