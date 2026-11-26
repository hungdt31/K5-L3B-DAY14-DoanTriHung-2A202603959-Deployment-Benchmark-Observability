"""
Kết nối Redis dùng chung cho rate limiter và cost guard.

  • Đọc REDIS_URL từ config
  • App vẫn KHỞI ĐỘNG ĐƯỢC khi Redis chưa sẵn sàng: redis.from_url() chỉ tạo connection pool,
    chưa mở kết nối nào cho tới lệnh đầu tiên.
  • Timeout ngắn để /ready và /ask không bị treo khi Redis chết.
"""
import redis

from app.config import settings

r = redis.from_url(
    settings.redis_url,
    decode_responses=True,
    socket_connect_timeout=2,
    socket_timeout=2,
    health_check_interval=30,
)


def redis_healthy() -> bool:
    """Dùng cho GET /ready: Redis không kết nối được → False."""
    try:
        return bool(r.ping())
    except redis.RedisError:
        return False
