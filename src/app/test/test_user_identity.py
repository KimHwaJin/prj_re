import pytest
from pydantic import ValidationError

from app.core.enums import UserRole
from app.schemas.common.user_schema import UserCreate, UserUpdate


def test_public_identity_and_display_name_are_distinct():
    user = UserCreate(user_id=" User.One@Example.COM ", user_name="  홍  길동  ")
    assert user.user_id == "user.one@example.com"
    assert user.user_name == "홍 길동"
    assert user.role == UserRole.USER


@pytest.mark.parametrize("data", [
    {"user_id": "me"}, {"user_id": "user/a"}, {"user_id": "a" * 101},
    {"user_id": ""}, {"role": "owner"}, {"user_name": "  "}, {"extra": "value"},
])
def test_registration_rejects_invalid_contract(data):
    with pytest.raises(ValidationError):
        UserCreate.model_validate({"user_id": "user-a", "user_name": "User", **data})


@pytest.mark.parametrize("data", [{}, {"role": None}, {"user_name": None}, {"user_id": "change"}, {"user_name": " "}])
def test_patch_rejects_empty_null_or_identity_change(data):
    with pytest.raises(ValidationError):
        UserUpdate.model_validate(data)


def test_every_user_api_documents_header_identity(monkeypatch):
    import service_settings
    from service_bootstrap import create_app
    monkeypatch.setattr(service_settings, "_snapshot", None)
    app = create_app(service_settings.load_settings(config={"AGENT_WORKER_ENABLED": False,
        "TASK_RECONCILER_ENABLED": False}, environ={}))
    schema = app.openapi()
    assert schema["components"]["securitySchemes"] == {
        "UserIdentity": {"type": "apiKey", "in": "header", "name": "X-User-Id"}}
    for path, operations in schema["paths"].items():
        if path.startswith("/api/v1/"):
            for method, operation in operations.items():
                if method in {"get", "post", "patch", "delete", "put"}:
                    assert operation["security"] == [{"UserIdentity": []}], (path, method)
    assert "/api/v1/users/me" in schema["paths"]
    assert "/api/v1/users/by-name/{user_name}" not in schema["paths"]
