from django.core.cache import cache

LIMITS = {"register": 8, "login": 12, "fixture": 12, "history": 12}
WINDOW_SECONDS = 60


def limited(endpoint, identity):
    """Fixed windows, atomic LocMem operations in the one local Web process."""
    key = f"s01:{endpoint}:{identity}"
    while not cache.add(key, 1, WINDOW_SECONDS):
        try:
            return cache.incr(key) > LIMITS[endpoint]
        except ValueError:
            # The first request's window expired between add and incr.
            continue
    return False
