def backend_url(url: str) -> str:
    assert url.startswith("/photos/")
    return url.replace("/photos/", "/api/v1/", 1)
