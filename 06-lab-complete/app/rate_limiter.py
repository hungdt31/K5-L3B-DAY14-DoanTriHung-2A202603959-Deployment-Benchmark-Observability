"""
Rate limiting (tiêu chí 7).

  • Mỗi API key tối đa RATE_LIMIT_PER_MINUTE request trong 60 giây (sliding window log)
  • Vượt giới hạn → HTTP 429 kèm header Retry-After
  • State lưu trong Redis (sorted set, score = timestamp ms) nên mọi worker / instance dùng chung
    và vẫn còn sau khi restart agent.
  • Toàn bộ "dọn cũ → đếm → thêm" chạy trong MỘT Lua script: Redis chạy script tuần tự nên
    2 worker nhận request cùng lúc không thể cùng lọt qua khi chỉ còn 1 slot.
"""
import hashlib
import math
import time
import uuid

from fastapi import HTTPException

from app.config import settings
from app.redis_client import r

WINDOW_SECONDS = 60

# KEYS[1] = key của client; ARGV = now_ms, window_ms, limit, member
# Trả về {allowed (1/0), số request còn lại, ms tới khi slot cũ nhất hết hạn}
_SLIDING_WINDOW = r.register_script("""
local key, now, window, limit = KEYS[1], tonumber(ARGV[1]), tonumber(ARGV[2]), tonumber(ARGV[3])
redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
local count = redis.call('ZCARD', key)
if count >= limit then
  local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
  return {0, 0, tonumber(oldest[2]) + window - now}
end
redis.call('ZADD', key, now, ARGV[4])
redis.call('PEXPIRE', key, window)
return {1, limit - count - 1, 0}
""")


def _bucket_key(api_key: str) -> str:
    # Không lưu API key dạng rõ trong Redis — chỉ lưu hash
    return "ratelimit:" + hashlib.sha256(api_key.encode()).hexdigest()[:16]


def check_rate_limit(key: str) -> int:
    """Raise 429 nếu vượt giới hạn; trả về số request còn lại trong cửa sổ hiện tại."""
    limit = settings.rate_limit_per_minute
    now_ms = int(time.time() * 1000)
    allowed, remaining, retry_ms = _SLIDING_WINDOW(
        keys=[_bucket_key(key)],
        args=[now_ms, WINDOW_SECONDS * 1000, limit, f"{now_ms}-{uuid.uuid4().hex[:8]}"],
    )
    if not allowed:
        retry_after = max(1, math.ceil(retry_ms / 1000))
        raise HTTPException(
            status_code=429,
            detail={"error": "Rate limit exceeded", "limit_per_minute": limit,
                    "retry_after_seconds": retry_after},
            headers={"Retry-After": str(retry_after),
                     "X-RateLimit-Limit": str(limit),
                     "X-RateLimit-Remaining": "0"},
        )
    return int(remaining)
