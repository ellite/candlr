from slowapi import Limiter
from slowapi.util import get_remote_address

# Single shared limiter instance imported by main.py (app setup) and by
# routers that need per-endpoint rate limits. Keyed by client IP, in-memory
# storage - suitable for a single-instance deploy.
limiter = Limiter(key_func=get_remote_address)
