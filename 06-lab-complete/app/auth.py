"""
API Key authentication (tiêu chí 6).

  • Client gửi header  X-API-Key: <key>
  • Key phải trùng biến môi trường AGENT_API_KEY
  • Thiếu hoặc sai key → HTTP 401 (không phải 403 hay 422)
"""
import hmac

from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader

from app.config import settings

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

_UNAUTHORIZED = HTTPException(
    status_code=401,
    detail="Missing or invalid API key. Include header: X-API-Key: <your-key>",
    headers={"WWW-Authenticate": "ApiKey"},
)


def verify_api_key(api_key: str | None = Security(api_key_header)) -> str:
    """Dependency cho FastAPI — dùng trong /ask: Depends(verify_api_key)."""
    expected = settings.agent_api_key
    # Chưa cấu hình key (chỉ xảy ra ở development) → từ chối hết, không bao giờ "mở cửa".
    if not api_key or not expected:
        raise _UNAUTHORIZED
    # compare_digest: thời gian so sánh không phụ thuộc vị trí ký tự sai → chống timing attack
    if not hmac.compare_digest(api_key.encode(), expected.encode()):
        raise _UNAUTHORIZED
    return api_key
