"""Only normal Flask static files and hashed, compiled assets are public."""
import re
from flask import request


def is_public_asset():
    if request.endpoint == 'static':
        return True
    return request.endpoint in {'inventory_react_app', 'operations_management_react_app'} and bool(
        re.fullmatch(r'assets/[\w.-]+-[\w-]{8,}\.(?:js|css|woff2?|png|svg|jpg|webp)',
                     (request.view_args or {}).get('path', '')))
