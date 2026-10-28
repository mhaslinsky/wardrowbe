import runpy

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.routing import APIRoute, _IncludedRouter
from starlette.routing import BaseRoute, Mount, Route

from app import config, main
from app.config import Settings
from app.utils.auth import get_current_user

PUBLIC_ROUTES = [
    ("GET", "/api/v1/health", "Process health probe"),
    ("GET", "/api/v1/health/ready", "Dependency readiness probe"),
    ("GET", "/api/v1/health/features", "Available feature flags"),
    ("GET", "/api/v1/health/ai", "AI service health probe"),
    ("GET", "/api/v1/capabilities", "Client capability discovery"),
    ("GET", "/api/v1/auth/mobile-callback", "Mobile sign-in redirect"),
    ("GET", "/api/v1/auth/config", "Sign-in provider discovery"),
    ("GET", "/api/v1/auth/status", "Sign-in status discovery"),
    ("POST", "/api/v1/auth/sync", "Validates provider identity before creating a session"),
    ("POST", "/api/v1/ha/wore-today", "Checks the Home Assistant token in the handler"),
    ("POST", "/api/v1/ha/laundry-done", "Checks the Home Assistant token in the handler"),
]
DOCS_ROUTES = [
    ("GET", "/docs", "Development Swagger UI"),
    ("HEAD", "/docs", "Development Swagger UI"),
    ("GET", "/docs/oauth2-redirect", "Development Swagger OAuth redirect"),
    ("HEAD", "/docs/oauth2-redirect", "Development Swagger OAuth redirect"),
    ("GET", "/redoc", "Development ReDoc UI"),
    ("HEAD", "/redoc", "Development ReDoc UI"),
    ("GET", "/openapi.json", "Development OpenAPI schema"),
    ("HEAD", "/openapi.json", "Development OpenAPI schema"),
]


def _effective_routes(routes):
    for route in routes:
        if type(route) is _IncludedRouter:
            # Validate originals first: FastAPI silently skips unsupported route classes.
            list(_effective_routes(route.original_router.routes))
            contexts = list(route.effective_route_contexts())
            assert contexts, "Included router has no effective routes"
            for context in contexts:
                assert type(context.original_route) in (APIRoute, Route), "Unknown route class"
                yield (
                    context if type(context.original_route) is APIRoute else context.starlette_route
                )
        else:
            assert type(route) in (APIRoute, Route), f"Unknown route class: {type(route)}"
            yield route


def _signed_in(dependant):
    assert dependant is not None, "Missing dependency graph"
    return dependant.call is get_current_user or any(
        _signed_in(child) for child in dependant.dependencies
    )


def _assert_inventory(application, exemptions, docs):
    observed_public = set()
    observed_docs = set()
    for route in _effective_routes(application.routes):
        assert route.methods, f"Missing HTTP methods: {route.path}"
        pairs = {(method, route.path) for method in route.methods}
        if type(route) is Route:
            observed_docs.update(pairs)
        elif not _signed_in(route.dependant):
            observed_public.update(pairs)
    expected_public = {(method, path) for method, path, reason in exemptions if reason.strip()}
    expected_docs = {(method, path) for method, path, reason in docs if reason.strip()}
    assert observed_public == expected_public, (
        f"Public routes differ: {observed_public ^ expected_public}"
    )
    assert observed_docs == expected_docs, f"Docs routes differ: {observed_docs ^ expected_docs}"


def test_real_app_route_inventory():
    _assert_inventory(main.app, PUBLIC_ROUTES, DOCS_ROUTES)


def test_production_has_no_docs(monkeypatch):
    settings = Settings(debug=False, oidc_issuer_url="", forward_auth_secret="")
    assert not settings.dev_mode
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    production_app = runpy.run_path(main.__file__)["app"]
    _assert_inventory(production_app, PUBLIC_ROUTES, [])


async def _endpoint():
    return {"ok": True}


async def _indirect_sign_in(user=Depends(get_current_user)):
    return user


def _control_app(protected=True):
    application = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    child = APIRouter()
    child.add_api_route("/private", _endpoint, methods=["GET"])
    parent = APIRouter()
    parent.include_router(
        child, prefix="/nested", dependencies=[Depends(_indirect_sign_in)] if protected else []
    )
    application.include_router(parent, prefix="/api")
    return application


@pytest.mark.parametrize("control", ["added public route", "removed sign-in", "removed exemption"])
def test_inventory_red_controls(control):
    application = _control_app()
    _assert_inventory(application, [], [])
    exemptions = []
    if control == "added public route":
        application.add_api_route("/unexpected", _endpoint, methods=["POST"])
    elif control == "removed sign-in":
        application = _control_app(protected=False)
    else:
        application.add_api_route("/allowed", _endpoint, methods=["GET"])
        exemptions = [("GET", "/allowed", "Control exemption")]
        _assert_inventory(application, exemptions, [])
        exemptions = []
    with pytest.raises(AssertionError, match="Public routes differ") as failure:
        _assert_inventory(application, exemptions, [])
    print(f"RED CONTROL {control}: {failure.value}")


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("route_class", [BaseRoute, Mount])
def test_unknown_route_classes_fail(nested, route_class):
    application = _control_app()
    router = APIRouter()
    router.routes.append(
        route_class("/mounted", app=_endpoint) if route_class is Mount else BaseRoute()
    )
    if nested:
        application.include_router(router)
    else:
        application.routes.extend(router.routes)
    with pytest.raises(AssertionError, match="Unknown route class"):
        _assert_inventory(application, [], [])


def test_missing_traversal_support_fails(monkeypatch):
    application = _control_app()
    monkeypatch.setattr(_IncludedRouter, "effective_route_contexts", None)
    with pytest.raises(TypeError):
        _assert_inventory(application, [], [])
