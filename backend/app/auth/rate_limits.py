import hashlib
import hmac
import math

from fastapi import HTTPException
from redis.exceptions import RedisError

from app.config import settings

LIMIT_SCRIPT = """
local count = redis.call('INCR', KEYS[1])
local ttl = redis.call('PTTL', KEYS[1])
if count == 1 or ttl < 0 then
    redis.call('PEXPIRE', KEYS[1], ARGV[1])
    ttl = tonumber(ARGV[1])
end
return {count, ttl}
"""


async def rate_limit(redis, identity, limit, window):
    digest = hmac.new(
        settings.jwt_secret_key.encode(), identity.encode(), hashlib.sha256
    ).hexdigest()
    key = settings.auth_rate_prefix + digest
    try:
        count, ttl = await redis.eval(LIMIT_SCRIPT, 1, key, max(1, int(window * 1000)))
    except (RedisError, OSError, TimeoutError):
        raise HTTPException(
            503,
            detail={
                "code": "RATE_LIMIT_UNAVAILABLE",
                "message": "Rate limiter unavailable",
            },
        ) from None
    if count > limit:
        raise HTTPException(
            429,
            detail={"code": "RATE_LIMITED", "message": "Too many requests"},
            headers={"Retry-After": str(max(1, math.ceil(ttl / 1000)))},
        )


def client_ip(request):
    # Forwarded headers are NOT parsed here. Configure trusted ASGI proxies explicitly.
    return request.client.host if request.client else "unknown"
