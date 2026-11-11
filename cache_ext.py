from flask_caching import Cache

# Shared cache instance. Configured in app.py to allow swapping cache backends.
cache = Cache()
