"""Loopback diagnostics only: SDK verdict fixture, production Redis/cookie/CSRF.

Never installed in the service application or packaged wheel. The corporate SDK
is unavailable outside the closed network; this fixture does not validate SSO.
"""
from urllib.parse import urlparse
from service_auth.sso.contracts import VerifiedEmployee


def configure_cookie_auth(config, namespace, port):
    origin = f"http://127.0.0.1:{port}"
    config.update(SSO_PUBLIC_API_ORIGIN=origin, SSO_FRONTEND_ORIGIN=origin,
        SSO_COOKIE_SECURE=False, SSO_NAMESPACE=namespace + ":sso",
        SSO_ALLOWED_ORIGINS=["https://sso.example.test"], SSO_AUTO_REGISTER=True)


def install_employee_fixture(app, employee_id):
    assert urlparse(app.state.sso.settings.public_api_origin).hostname in {"127.0.0.1", "localhost"}
    class VerifiedVerdictFixture:
        async def verify(self, request):
            return VerifiedEmployee(employee_id, "Integration fixture")
        async def login_url(self, request, return_url):
            raise AssertionError("Corporate browser roundtrip is outside this diagnostic")
    assert not app.dependency_overrides, "Do not bypass production identity dependencies"
    app.state.sso.adapter = VerifiedVerdictFixture()


async def sign_in(client):
    response = await client.get("/api/v1/auth/login/sso", follow_redirects=False)
    assert response.status_code == 302, response.text
    me = await client.get("/api/v1/users/me")
    assert me.status_code == 200, me.text
    user = me.json()
    assert user["role"] == "user" and user["default_project_id"]
    return user, {"X-CSRF-Token": user["csrf_token"]}


def write_private_result(path, value):
    """Full execution output belongs to the operator, never a credential log."""
    import json
    import os
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        os.fchmod(stream.fileno(), 0o600)
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
