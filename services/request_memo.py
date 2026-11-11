"""Reuse pure read calculations only within one GET request."""
from functools import wraps
from flask import g, has_request_context, request


def request_memo(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not has_request_context() or request.method != 'GET':
            return fn(*args, **kwargs)
        key = (fn.__module__, fn.__name__, repr(args), repr(sorted(kwargs.items())))
        values = getattr(g, '_read_calculations', None)
        if values is None:
            values = g._read_calculations = {}
        if key not in values:
            values[key] = fn(*args, **kwargs)
        return values[key]
    return wrapper
