"""Same-origin Swagger login and CSRF injection, with explicit cookie security metadata."""

import json

from fastapi import Request
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse
from fastapi import routing
from fastapi.routing import APIRoute

from .dependencies import get_login_session


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
        # New FastAPI versions retain included routers rather than flattening app.routes.
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
    script = """
const apiPrefix = PREFIX;
let csrfToken = null;
const status = () => document.getElementById('sso-status');
async function refreshIdentity() {
  csrfToken = null;
  try {
    const response = await fetch(apiPrefix + '/users/me', {credentials:'same-origin', cache:'no-store'});
    if (!response.ok) {
      status().textContent = response.status === 401 ? '로그인이 필요합니다' : '로그인 상태 조회 실패';
      return false;
    }
    const user = await response.json();
    csrfToken = user.csrf_token;
    status().textContent = user.user_name + ' · ' + user.role;
    return true;
  } catch (_) { status().textContent = '로그인 상태 조회 실패'; return false; }
}
async function ssoRequestInterceptor(request) {
  const url = new URL(request.url, window.location.href);
  // Never send the CSRF secret to a remote specification or another API origin.
  if (url.origin !== window.location.origin || !url.pathname.startsWith(apiPrefix + '/')) return request;
  request.credentials = 'same-origin';
  if (!['GET','HEAD','OPTIONS'].includes((request.method || 'GET').toUpperCase())) {
    if (!csrfToken && !(await refreshIdentity())) throw new Error('SSO 로그인 후 로그인 상태 확인 버튼을 누르세요.');
    request.headers = request.headers || {};
    request.headers['X-CSRF-Token'] = csrfToken;
  }
  return request;
}
""".replace("PREFIX", prefix)

    async def docs(request: Request):
        # Existing Swagger assets are retained; a closed-network deployment can pass local assets
        # through its supplied Swagger assembly instead of this service-owned page.
        response = get_swagger_ui_html(
            openapi_url=app.openapi_url,
            title="SSO API 테스트",
            swagger_ui_parameters={
                "withCredentials": True,
                "persistAuthorization": False,
                "validatorUrl": None,
            },
        )
        html = response.body.decode()
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
document.getElementById('sso-login').href = apiPrefix + '/auth/login/sso?target=docs';
document.getElementById('sso-refresh').addEventListener('click', refreshIdentity);
refreshIdentity();
"""
        html = html.replace("</body>", "<script>" + tail + "</script></body>")
        return HTMLResponse(html, headers={"Cache-Control": "no-store"})

    app.add_api_route(
        docs_path, docs, methods=["GET"], include_in_schema=False
    )
