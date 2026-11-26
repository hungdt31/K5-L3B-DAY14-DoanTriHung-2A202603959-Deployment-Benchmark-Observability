"""
Config — 12-Factor: mọi cấu hình đọc từ biến môi trường.

  • Mọi giá trị đều lấy từ env; mặc định chỉ dành cho chạy local và KHÔNG có secret.
  • AGENT_API_KEY không có mặc định: thiếu key ở staging/production thì dừng ngay lúc khởi động
    (fail fast), ở development thì cảnh báo và /ask từ chối mọi request (401).
  • Danh sách biến đầy đủ: xem .env.example.
"""
import os
from dataclasses import dataclass, field


def _int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        return int(raw)
    except ValueError:
        raise RuntimeError(f"{name} phải là số nguyên, nhận được {raw!r}") from None


def _float(name: str, default: float) -> float:
    raw = os.getenv(name, str(default))
    try:
        return float(raw)
    except ValueError:
        raise RuntimeError(f"{name} phải là số thực, nhận được {raw!r}") from None


@dataclass(frozen=True)
class Settings:
    port: int = field(default_factory=lambda: _int("PORT", 8000))
    environment: str = field(default_factory=lambda: os.getenv("ENVIRONMENT", "development").lower())
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO").upper())

    # Secret: không có giá trị mặc định
    agent_api_key: str = field(default_factory=lambda: os.getenv("AGENT_API_KEY", ""))

    # Redis: nơi giữ toàn bộ state (rate limit, chi phí) để agent stateless
    redis_url: str = field(default_factory=lambda: os.getenv("REDIS_URL", "redis://localhost:6379/0"))

    rate_limit_per_minute: int = field(default_factory=lambda: _int("RATE_LIMIT_PER_MINUTE", 10))
    daily_budget_usd: float = field(default_factory=lambda: _float("DAILY_BUDGET_USD", 1.0))

    def validate(self) -> "Settings":
        if self.rate_limit_per_minute <= 0:
            raise RuntimeError("RATE_LIMIT_PER_MINUTE phải > 0")
        if self.daily_budget_usd <= 0:
            raise RuntimeError("DAILY_BUDGET_USD phải > 0")
        if not self.agent_api_key and self.environment in ("staging", "production"):
            raise RuntimeError(f"AGENT_API_KEY bắt buộc khi ENVIRONMENT={self.environment}")
        return self


settings = Settings().validate()
