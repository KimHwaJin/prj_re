"""Compatibility ASGI entrypoint; root app.py is the preferred launcher."""
from dtest.bootstrap import create_app

app = create_app()
