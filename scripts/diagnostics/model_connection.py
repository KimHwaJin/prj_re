"""Private model-only input for local diagnostics; never deployed/imported by app."""

from contextlib import contextmanager
from pathlib import Path
import socket
from urllib.parse import urlsplit

from dotenv import dotenv_values

MODEL_KEYS = (
    "MODEL_NAME",
    "API_BASE_URL",
    "MODEL_API_KEY",
    "MODEL_ENABLE_THINKING",
    "MODEL_TIMEOUT_SECONDS",
    "MODEL_MAX_RETRIES",
    "MODEL_TEMPERATURE",
    "MODEL_STRUCTURED_OUTPUT_MODE",
)


def load_model_env(path: Path) -> dict:
    values = dotenv_values(path, interpolate=False)
    result = {
        key: values[key] for key in MODEL_KEYS if values.get(key) is not None
    }
    validate_real_model(result)
    return {**result, "MODEL_PROVIDER": "openai_compatible"}


def validate_real_model(values):
    # Error text never contains credentials or the supplied endpoint.
    if not all(
        values.get(key)
        for key in ("MODEL_NAME", "API_BASE_URL", "MODEL_API_KEY")
    ):
        raise ValueError(
            "Real model requires MODEL_NAME, API_BASE_URL and MODEL_API_KEY"
        )
    endpoint = urlsplit(values["API_BASE_URL"])
    if (
        endpoint.scheme not in {"http", "https"}
        or not endpoint.hostname
        or endpoint.username
        or endpoint.password
    ):
        raise ValueError(
            "Real model requires an HTTP(S) endpoint without "
            "embedded credentials"
        )
    if (
        endpoint.hostname == "fixture.invalid"
        or values["MODEL_NAME"] == "contract-fixture"
    ):
        raise ValueError("Fixture settings cannot be used for real model mode")
    if (
        values.get("MODEL_PROVIDER", "openai_compatible")
        != "openai_compatible"
    ):
        raise ValueError("Real mode requires MODEL_PROVIDER=openai_compatible")


@contextmanager
def model_host_alias():
    """User-provided local alias, scoped to this diagnostic process only."""
    original = socket.getaddrinfo

    def resolve(host, *args, **kwargs):
        return original(
            {"model.frodo.com": "10.250.110.99"}.get(host, host),
            *args,
            **kwargs,
        )

    socket.getaddrinfo = resolve
    try:
        yield
    finally:
        socket.getaddrinfo = original
