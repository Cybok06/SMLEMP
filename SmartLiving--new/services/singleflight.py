"""Bounded process-local locks: followers recheck the existing section cache."""
from functools import wraps
from threading import RLock
from flask import request

_locks = [RLock() for _ in range(64)]


def singleflight(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        # Does not cache responses or bypass the authorization inside the view.
        key = (fn.__module__, fn.__name__, tuple(sorted(request.args.items(multi=True))))
        with _locks[hash(key) % len(_locks)]:
            return fn(*args, **kwargs)
    return wrapper
