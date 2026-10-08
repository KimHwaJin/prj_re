"""WSGI-style request metadata for the synchronous corporate SDK.

This is a metadata snapshot, not a WSGI gateway. It does not consume the
ASGI request body or expose fake input streams and process environments.
"""

from fastapi import Request


def request_environ(request: Request) -> dict[str, str]:
    """Translate ASGI request metadata; proxy trust stays with the server."""
    scope = request.scope
    root_path = scope.get("root_path", "")
    path = scope.get("path", "")
    if root_path and (path == root_path or path.startswith(root_path + "/")):
        path = path[len(root_path) :]
    server = scope.get("server")
    environ = {
        "REQUEST_METHOD": scope.get("method", "GET"),
        "SCRIPT_NAME": root_path.encode("utf-8").decode("latin-1"),
        "PATH_INFO": path.encode("utf-8").decode("latin-1"),
        "QUERY_STRING": scope.get("query_string", b"").decode("latin-1"),
        "SERVER_PROTOCOL": "HTTP/" + scope.get("http_version", "1.1"),
        "SERVER_NAME": server[0] if server else "",
        "SERVER_PORT": (
            str(server[1]) if server and server[1] is not None else ""
        ),
        "wsgi.url_scheme": scope.get("scheme", "http"),
    }
    client = scope.get("client")
    if client:
        environ["REMOTE_ADDR"] = client[0]
        environ["REMOTE_PORT"] = str(client[1])
    for name, value in scope.get("headers", []):
        header = name.decode("latin-1").upper().replace("-", "_")
        key = (
            header
            if header in {"CONTENT_TYPE", "CONTENT_LENGTH"}
            else "HTTP_" + header
        )
        text = value.decode("latin-1")
        if key in environ:
            separator = "; " if key == "HTTP_COOKIE" else ","
            environ[key] += separator + text
        else:
            environ[key] = text
    return environ
