"""Atomic Redis fixed-window counters with no sliding expiration race."""
import hashlib

import redis

from app.errors import ApiError

COUNTER = '''
local count = redis.call('INCR', KEYS[1])
if count == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
return {count, redis.call('TTL', KEYS[1])}
'''


class RateLimiter:
    def __init__(self, settings):
        self.enabled = settings.rate_limit_enabled
        self.redis = redis.Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2)
        self.counter = self.redis.register_script(COUNTER)

    def check(self, identifier, limit, window):
        if not self.enabled:
            return
        key = 'saiv:rate_limit:' + hashlib.sha256(identifier.encode()).hexdigest() + f':{window}'
        try:
            count, ttl = self.counter(keys=[key], args=[window])
        except redis.RedisError:
            raise ApiError(503, 'Request protection service is unavailable', 'RATE_LIMIT_UNAVAILABLE') from None
        if count > limit:
            error = ApiError(429, 'Too many requests. Please try again later', 'RATE_LIMITED')
            error.headers = {'Retry-After': str(max(1, ttl))}
            raise error

    def close(self):
        self.redis.close()
