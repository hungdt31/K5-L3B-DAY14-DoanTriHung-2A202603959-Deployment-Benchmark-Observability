# Deployment Information

## Public URL

https://agent-production-51bf7a38.up.railway.app

## Platform

**Railway**: project `day14-agent`, environment `production`, region mặc định của Railway.

| Service | Nguồn | Ghi chú |
|---|---|---|
| `agent` | `06-lab-complete/` (`railway up`, build bằng `Dockerfile` theo `railway.toml`) | 2 uvicorn worker, healthcheck `/health` |
| `Redis` | Railway Redis (`railway add --database redis`), có volume `redis-volume` | Chỉ truy cập qua private network của project |

Cách deploy đã dùng:

```bash
npm i -g @railway/cli
railway login
cd 06-lab-complete
railway init --name day14-agent
railway add --database redis
railway add --service agent
# secret truyền qua stdin, không xuất hiện trong lịch sử shell hay trong repo
python3 -c "import secrets;print(secrets.token_urlsafe(32), end='')" | railway variable set AGENT_API_KEY --stdin -s agent
railway variable set 'REDIS_URL=${{Redis.REDIS_URL}}' ENVIRONMENT=production RATE_LIMIT_PER_MINUTE=10 DAILY_BUDGET_USD=1.0 LOG_LEVEL=INFO -s agent
railway up -s agent --ci
railway domain -s agent
```

## Test Commands

```bash
export BASE=https://agent-production-51bf7a38.up.railway.app
# Lấy key từ Railway (không ghi key vào file):
export AGENT_API_KEY=$(railway variable list -s agent --json | python3 -c 'import sys,json;print(json.load(sys.stdin)["AGENT_API_KEY"])')
```

### 4 lệnh tự kiểm (CP6): chỉ in mã HTTP

```bash
curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/health"     # 200
curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/ready"      # 200
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -d '{"question":"Hello"}'   # 401
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -H "X-API-Key: $AGENT_API_KEY" -d '{"question":"Hello"}'   # 200
```

Kết quả (2026-11-26):

```text
200
200
401
200
```

### Health check

```bash
curl https://agent-production-51bf7a38.up.railway.app/health
# {"status":"ok","version":"1.0.0","environment":"production"}

curl https://agent-production-51bf7a38.up.railway.app/ready
# {"ready":true}
```

### API test (with authentication)

```bash
curl -X POST https://agent-production-51bf7a38.up.railway.app/ask \
  -H "X-API-Key: $AGENT_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"question": "What is deployment?"}'
# {"question":"What is deployment?","answer":"Deployment là quá trình đưa code từ máy bạn lên server để người khác dùng được.","usage":{"input_tokens":4,"output_tokens":21,"cost_usd":1.32e-05,"requests_remaining":8}}
```

### Rate limit trên cloud (`RATE_LIMIT_PER_MINUTE=10`)

12 request liên tiếp sau 2 request ở trên:

```text
200 200 200 200 200 200 200 200 429 429 429 429
HTTP/2 429
retry-after: 47
{"detail":{"error":"Rate limit exceeded","limit_per_minute":10,"retry_after_seconds":47}}
```

### Script chấm với public URL

```text
$ python check_production_ready.py --public-url https://agent-production-51bf7a38.up.railway.app
...
✅ 12. Public URL ready (Railway / Render config)
     ✅ railway.toml: builder + healthcheckPath = "/health"
     ✅ App lắng nghe theo biến PORT do platform cấp
     ✅ Public URL: GET https://agent-production-51bf7a38.up.railway.app/health → 200
...
  Result: 12/12 tiêu chí đạt · 44/44 checks passed
  🎉 PASS 12/12 — PRODUCTION READY! Deploy nào!
```

## Environment Variables Set

Đặt trên service `agent` của Railway, **không** có trong code hay repo:

| Biến | Giá trị | Ghi chú |
|---|---|---|
| `AGENT_API_KEY` | *(secret, 43 ký tự, sinh ngẫu nhiên)* | Đặt qua `--stdin` |
| `REDIS_URL` | `${{Redis.REDIS_URL}}` | Reference variable: Railway tự thay bằng URL private của service Redis; đổi mật khẩu Redis thì agent tự nhận giá trị mới |
| `ENVIRONMENT` | `production` | Bật fail-fast khi thiếu key, ẩn `/docs` (`GET /docs` → 404) |
| `RATE_LIMIT_PER_MINUTE` | `10` | |
| `DAILY_BUDGET_USD` | `1.0` | |
| `LOG_LEVEL` | `INFO` | |
| `PORT` | *(Railway tự cấp, 8080)* | `CMD` trong Dockerfile đọc `${PORT:-8000}`; log: `Uvicorn running on http://0.0.0.0:8080` |

Log khởi động trên Railway (Railway tự parse log JSON thành các field):

```text
[INFO] ts="2026-11-26T09:45:46.163+00:00" logger="agent" event="startup" version="1.0.0" environment="production" rate_limit_per_minute=10 daily_budget_usd=1 redis_ok=true api_key_configured=true
```

## Screenshots

- [Deployment dashboard](screenshots/dashboard.png)
- [Service running](screenshots/running.png)
- [Test results](screenshots/test.png)
