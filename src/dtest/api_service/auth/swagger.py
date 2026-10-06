"""Same-origin Swagger login and CSRF injection, with explicit cookie
security metadata.
"""

import json
from functools import lru_cache
from importlib.resources import files

from fastapi import Request, routing
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse
from fastapi.routing import APIRoute

from .dependencies import get_login_session


@lru_cache(maxsize=1)
def _login_script() -> str:
    return (
        files("dtest.api_service")
        .joinpath("web/static/swagger-auth.js")
        .read_text(encoding="utf-8")
    )


def _requires_login(dependant):
    return dependant.call is get_login_session or any(
        _requires_login(child) for child in dependant.dependencies
    )


def attach_swagger(app, runtime, *, docs_path: str):
    original_openapi = app.openapi

    def openapi():
        schema = original_openapi()
        schemes = schema.setdefault("components", {}).setdefault(
            "securitySchemes", {}
        )
        schemes["LoginSession"] = {
            "type": "apiKey",
            "in": "cookie",
            "name": runtime.settings.cookie_name,
            "description": (
                "Browser-managed cookie issued by corporate SSO login. "
                "Do not paste a cookie into "
                "Authorize."
            ),
        }
        # New FastAPI versions retain included routers rather than flattening
        # app.routes.
        contexts = getattr(
            routing, "iter_route_contexts", lambda routes: routes
        )(app.routes)
        for context in contexts:
            route = getattr(context, "original_route", context)
            if isinstance(route, APIRoute) and _requires_login(
                context.dependant
            ):
                operations = schema.get("paths", {}).get(
                    context.path_format, {}
                )
                for method in context.methods:
                    operation = operations.get(method.lower())
                    if operation is not None:
                        existing = operation.get("security") or [{}]
                        operation["security"] = [
                            {**requirement, "LoginSession": []}
                            for requirement in existing
                        ]
        return schema

    app.openapi = openapi
    # JSON escaping prevents config strings from breaking an inline script tag.
    prefix = json.dumps(runtime.api_prefix).replace("<", "\\u003c")
    script = _login_script().replace("PREFIX", prefix)

    async def docs(request: Request):
        # Existing Swagger assets are retained; a closed-network deployment can
        # pass local assets
        # through its supplied Swagger assembly instead of this service-owned
        # page.
        response = get_swagger_ui_html(
            openapi_url=app.openapi_url,
            title="SSO API 테스트",
            swagger_ui_parameters={
                "withCredentials": True,
                "persistAuthorization": False,
                "validatorUrl": None,
            },
        )
        html = bytes(response.body).decode("utf-8")
        marker = "const ui = SwaggerUIBundle({"
        if marker not in html:
            raise RuntimeError("Unsupported Swagger HTML template")
        html = html.replace(
            marker,
            script
            + "\n"
            + marker
            + "\nrequestInterceptor: ssoRequestInterceptor,",
            1,
        )
        toolbar = (
            "<div style='padding:12px;font-family:sans-serif'>"
            "<a id='sso-login'>SSO 로그인</a> · "
            "<button id='sso-refresh' type='button'>로그인 상태 확인</button> "
            "<span id='sso-status'>확인 중</span></div>"
        )
        html = html.replace(
            '<div id="swagger-ui">', toolbar + '<div id="swagger-ui">', 1
        )
        tail = """
document.getElementById('sso-login').href =
  apiPrefix + '/auth/login/sso?target=docs';
document.getElementById('sso-refresh')
  .addEventListener('click', refreshIdentity);
refreshIdentity();
"""
        html = html.replace("</body>", "<script>" + tail + "</script></body>")
        return HTMLResponse(html, headers={"Cache-Control": "no-store"})

    app.add_api_route(
        docs_path, docs, methods=["GET"], include_in_schema=False
    )
