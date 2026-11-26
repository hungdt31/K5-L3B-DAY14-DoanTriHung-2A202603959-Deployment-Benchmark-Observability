"""
Cost guard (tiêu chí 8).

  • Cộng dồn chi phí LLM (tính theo token) của cả hệ thống vào Redis, theo ngày (UTC)
  • Tổng chi phí vượt DAILY_BUDGET_USD → HTTP 402
  • "Giữ chỗ trước, quyết toán sau":
      check_budget(estimated)  — TRƯỚC khi gọi LLM: atomic (Lua) kiểm tra
                                 đã_tiêu + ước_tính <= budget rồi cộng phần ước tính vào luôn,
                                 nên N request song song không thể cùng lọt qua khi budget sắp hết.
      record_cost(in, out, reserved) — SAU khi gọi LLM: cộng phần chênh lệch (thật − ước tính).
  • Key theo ngày (cost:2026-11-26) tự hết hạn sau 2 ngày → sang ngày mới tự "reset".
"""
from datetime import datetime, timezone

from fastapi import HTTPException

from app.config import settings
from app.redis_client import r

# Giá USD / 1K tokens (tham khảo gpt-4o-mini)
PRICE_INPUT_PER_1K = 0.00015
PRICE_OUTPUT_PER_1K = 0.0006
KEY_TTL_SECONDS = 2 * 24 * 3600

# KEYS[1] = key chi phí hôm nay; ARGV = estimated_cost, budget, ttl
# Trả về {allowed (1/0), tổng đã tiêu (string)}
_RESERVE = r.register_script("""
local spent = tonumber(redis.call('GET', KEYS[1]) or '0')
local cost, budget = tonumber(ARGV[1]), tonumber(ARGV[2])
if spent + cost > budget then
  return {0, tostring(spent)}
end
local total = redis.call('INCRBYFLOAT', KEYS[1], cost)
redis.call('EXPIRE', KEYS[1], ARGV[3])
return {1, total}
""")


def _today_key() -> str:
    return f"cost:{datetime.now(timezone.utc):%Y-%m-%d}"


def estimate_tokens(text: str) -> int:
    # Mock: ~1.3 token / từ, làm tròn lên. LLM thật thì dùng tokenizer hoặc usage trả về.
    return max(1, int(len(text.split()) * 1.3) + 1)


def cost_usd(input_tokens: int, output_tokens: int) -> float:
    return input_tokens / 1000 * PRICE_INPUT_PER_1K + output_tokens / 1000 * PRICE_OUTPUT_PER_1K


def check_budget(estimated_cost: float = 0.0) -> None:
    """Gọi TRƯỚC khi gọi LLM. Hết budget → raise HTTPException(status_code=402)."""
    allowed, spent = _RESERVE(
        keys=[_today_key()],
        args=[estimated_cost, settings.daily_budget_usd, KEY_TTL_SECONDS],
    )
    if not allowed:
        raise HTTPException(
            status_code=402,
            detail={"error": "Daily budget exceeded",
                    "spent_usd": round(float(spent), 6),
                    "budget_usd": settings.daily_budget_usd,
                    "resets_at": "00:00 UTC"},
        )


def record_cost(input_tokens: int, output_tokens: int, reserved: float = 0.0) -> float:
    """Gọi SAU khi gọi LLM. Cộng chi phí thật (trừ phần đã giữ chỗ), trả về chi phí request."""
    actual = cost_usd(input_tokens, output_tokens)
    delta = actual - reserved
    if delta:
        key = _today_key()
        r.incrbyfloat(key, delta)
        r.expire(key, KEY_TTL_SECONDS)
    return actual


def release(reserved: float) -> None:
    """LLM lỗi → trả lại phần đã giữ chỗ."""
    if reserved:
        r.incrbyfloat(_today_key(), -reserved)
