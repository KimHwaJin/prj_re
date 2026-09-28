"""Compatibility ASGI entrypoint; root app.py is the preferred launcher."""
from service_bootstrap import create_app

app = create_app()
