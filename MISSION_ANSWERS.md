# Day 14 Lab - Mission Answers

> Môi trường: Linux (`os.name == posix`), Python 3.14.4 (venv `.venv/`), Docker 29.8.0, Docker Compose v5.5.1. `docker run --rm hello-world` chạy được.

## Part 1: Localhost vs Production

### Exercise 1.1: Anti-patterns found

Trong `01-localhost-vs-production/develop/app.py`:

| # | Anti-pattern | Dòng | Hậu quả | Bản production sửa thế nào |
|---|---|---|---|---|
| 1 | **Hardcode secret**: `OPENAI_API_KEY = "sk-hardcoded-..."` và `DATABASE_URL` có sẵn user/password | 17–18 | Push lên GitHub là lộ key và mật khẩu DB. Key vẫn nằm trong lịch sử commit dù sau đó có xoá. Muốn đổi key thì phải sửa code và deploy lại. | `config.py:27` đọc `os.getenv("OPENAI_API_KEY", "")`; trong code không còn secret nào |
| 2 | **Log ra secret**: `print(f"[DEBUG] Using key: {OPENAI_API_KEY}")` | 34 | Key nằm trong log, và ai đọc được log (log aggregator, nhân viên support) đều thấy key. Lúc chạy thử, log server in ra đúng dòng `[DEBUG] Using key: sk-hardcoded-fake-key-never-do-this` | `app.py:111-115` chỉ log `question_length` và `client_ip`, không log secret và cũng không log nguyên văn câu hỏi |
| 3 | **Dùng `print()` thay cho logging**: không có level, không có timestamp, không có cấu trúc | 33, 38, 48 | Log aggregator không lọc được theo level hay field. Không tắt được log debug trên production. Câu hỏi và câu trả lời của người dùng (có thể là dữ liệu cá nhân) bị in nguyên văn | `app.py:28-31` dùng `logging` với format JSON, level lấy theo `DEBUG` |
| 4 | **Config cứng trong code**: `DEBUG = True`, `MAX_TOKENS = 500` | 21–22 | Dev, staging và prod buộc phải dùng chung một giá trị, muốn đổi thì phải sửa code. Debug luôn bật | `config.py:19,29` đọc `DEBUG` và `MAX_TOKENS` từ env, mặc định `DEBUG=false` |
| 5 | **Không có health check** | 42–43 | Gọi `GET /health` thì nhận **404**. Platform (Railway, Render, K8s) không biết agent đã treo nên không restart, load balancer vẫn tiếp tục gửi traffic vào instance hỏng | `app.py:135-161` có `/health` (liveness) và `/ready` (readiness) |
| 6 | **Bind `host="localhost"`** | 51 | Chỉ nhận kết nối từ 127.0.0.1. Chạy trong container thì từ ngoài không gọi được (port-mapping của Docker không tới được) | `config.py:17` mặc định `HOST=0.0.0.0` |
| 7 | **Port cố định `8000`** | 52 | Railway và Render inject `PORT` qua env. App không đọc biến này thì platform không route được tới app | `config.py:18` đọc `PORT`. Mình đã thử `PORT=9000` và app nghe đúng `0.0.0.0:9000` |
| 8 | **`reload=True` luôn bật** | 53 | Tốn thêm một process watcher, theo dõi file system, có thể tự restart giữa chừng. Không ai dùng cái này trên production | `app.py:198` đặt `reload=settings.debug`, chỉ bật khi `DEBUG=true` |
| 9 | **Không xử lý shutdown**, không có lifecycle hook | cả file | Khi platform gửi SIGTERM (lúc deploy hay scale down), các request đang xử lý bị cắt ngang và kết nối DB không được đóng | `app.py:39-67` dùng `lifespan` (đặt `is_ready=False`, chờ request xong), `app.py:180-189` có handler cho SIGTERM |
| 10 | **Không validate input**: `question` nhận qua query string, không giới hạn độ dài | 30–31 | Câu hỏi bị ghi lại trong access log và URL. Gửi JSON body thì nhận 422 (đã thử) | `app.py:103-108` đọc JSON body, câu hỏi rỗng thì trả 422 kèm thông báo rõ ràng |

### Exercise 1.2: Chạy bản develop

```bash
cd 01-localhost-vs-production/develop
pip install -r requirements.txt
python app.py
```

Kết quả (bản develop nhận `question` qua query, xem mục *Lưu ý khi làm bài* số 3 trong README):

```text
$ curl -s -X POST "http://localhost:8000/ask?question=Hello" -w '\nHTTP %{http_code}\n'
{"answer":"Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận."}
HTTP 200

$ curl -s -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question":"Hello"}' -w '\nHTTP %{http_code}\n'
{"detail":[{"type":"missing","loc":["query","question"],"msg":"Field required","input":null}]}
HTTP 422

$ curl -s http://localhost:8000/health -w '\nHTTP %{http_code}\n'
{"detail":"Not Found"}
HTTP 404
```

Log phía server: secret bị in ra ngay trong lần gọi đầu tiên.

```text
INFO:     Uvicorn running on http://localhost:8000 (Press CTRL+C to quit)
INFO:     Started reloader process [172213] using WatchFiles
[DEBUG] Got question: Hello
[DEBUG] Using key: sk-hardcoded-fake-key-never-do-this
[DEBUG] Response: Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận.
```

**Quan sát:** app chạy được nhưng chưa sẵn sàng cho production. Nó chỉ nghe trên `localhost`, bật reloader, in key ra log và không có `/health`.

### Exercise 1.3: Comparison table

Chạy bản production (`cd ../production && cp .env.example .env && python app.py`):

```text
GET  /health  → 200 {"status":"ok","uptime_seconds":0.5,"version":"1.0.0","environment":"development",...}
GET  /ready   → 200 {"ready":true}
GET  /metrics → 200 {"uptime_seconds":0.5,"environment":"development","version":"1.0.0"}
POST /ask {"question":"Hello"} → 200 {"question":"Hello","answer":"...","model":"gpt-4o-mini"}
POST /ask {}                   → 422 {"detail":"question field is required"}
```

| Feature | Develop | Production | Tại sao quan trọng? |
|---|---|---|---|
| **Config** | Hardcode trong code (`develop/app.py:17-22`) | Đọc từ env qua `config.py` (dataclass `Settings`), có `validate()` dừng app ngay nếu `ENVIRONMENT=production` mà thiếu `AGENT_API_KEY` | Cùng một image chạy được ở dev, staging và prod, chỉ cần đổi env. Secret không vào Git. App thiếu config thì dừng ngay lúc khởi động, không đợi tới request đầu tiên mới lỗi |
| **Secrets** | Key và mật khẩu DB nằm trong code, còn bị in ra log | Lấy từ env, không log | Lộ key thì bị dùng chùa API (mất tiền) hoặc bị vào DB. Secret đã nằm trong lịch sử Git thì coi như đã lộ, phải đổi key mới |
| **Health check** | Không có (`/health` → 404) | `/health` (liveness: process còn sống, kèm uptime) và `/ready` (readiness: trả 503 khi chưa sẵn sàng hoặc đang tắt) | Platform dựa vào liveness để tự restart container bị treo, load balancer dựa vào readiness để chỉ gửi traffic vào instance sẵn sàng. Tách hai endpoint để instance đang khởi động hoặc đang tắt bị rút khỏi LB mà không bị restart |
| **Logging** | `print()` thô, có in secret và nội dung câu hỏi | `logging` format JSON, có level và timestamp, chỉ log metadata (`question_length`, `client_ip`) | Log JSON thì Loki, Datadog hay CloudWatch parse, lọc và đếm được. Có level để tắt debug trên prod. Không làm rò rỉ secret hay dữ liệu cá nhân |
| **Shutdown** | Tắt đột ngột, không có hook | Graceful: `lifespan` đặt `is_ready=False`, chờ request đang chạy xong, rồi mới đóng tài nguyên. Có handler SIGTERM | Mỗi lần deploy hay scale down, platform gửi SIGTERM. Tắt graceful thì không cắt ngang request của người dùng, không mất dữ liệu, kết nối DB và Redis được đóng sạch |
| **Host / Port** | `localhost:8000` cứng | `0.0.0.0` + `PORT` từ env | Trong container phải bind `0.0.0.0` thì mới nhận được traffic. Cloud platform tự chọn port và báo cho app qua `PORT` |
| **Reload / Debug** | `reload=True`, `DEBUG=True` luôn bật | Chỉ bật khi `DEBUG=true` | Reloader tốn tài nguyên và có thể tự restart giữa chừng. Debug mode có thể làm lộ chi tiết lỗi ra ngoài |
| **Input** | Query string, không kiểm tra | JSON body, rỗng thì trả 422 có thông báo | Trả mã lỗi rõ ràng. Câu hỏi không bị ghi lại trong URL hay access log |
| **CORS** | Không cấu hình | `ALLOWED_ORIGINS` từ env | Chỉ frontend được phép mới gọi được API từ trình duyệt |

### Ghi chú thêm khi chạy thử bản production

Đây là những điểm mình thấy khi chạy thật, không có sẵn trong đề:

1. **`.env` không được nạp.** `requirements.txt` có `python-dotenv` nhưng không file nào gọi `load_dotenv()`, và `config.py` chỉ dùng `os.getenv`. Vì vậy `cp .env.example .env` không có tác dụng gì: app chạy với giá trị mặc định trong code. Muốn đặt config thì phải `export` biến (hoặc dùng `env_file` của Docker Compose, hay biến môi trường trên platform). Cách sửa: thêm `from dotenv import load_dotenv; load_dotenv()` ở đầu `config.py`.
2. **Log JSON không hiện khi thiếu `OPENAI_API_KEY`.** `config.py:45` gọi `logging.warning(...)` lúc import. Lời gọi này tự chạy `basicConfig()` mặc định với level WARNING. Đến khi `app.py:28` gọi `basicConfig(...)` thì root logger đã có handler nên lời gọi đó bị bỏ qua, mọi `logger.info` bị nuốt mất. Mình đã kiểm tra: khi đặt `OPENAI_API_KEY=dummy` thì các dòng log JSON (`startup`, `agent_request`, `agent_response`, `Agent shutting down gracefully`, `Shutdown complete`) hiện đầy đủ. Cách sửa: gọi `basicConfig(..., force=True)` hoặc cấu hình logging trước khi import `config`.
3. **Log chưa phải JSON hợp lệ.** Format là `"msg":"%(message)s"` mà message lại là `json.dumps(...)`, nên log ra dạng `"msg":"{"event": "startup", ...}"` với dấu `"` không được escape. Log aggregator sẽ không parse được dòng này. Nên dùng thư viện như `python-json-logger` hoặc `structlog`.
4. **SIGTERM được xử lý graceful.** Mình gửi `kill -TERM <pid>` và log cho thấy uvicorn chạy `Shutting down` → `Waiting for application shutdown` → hook shutdown của `lifespan` → `Application shutdown complete` → `Finished server process`, rồi process thoát. Uvicorn tự cài signal handler riêng khi khởi động, nên `handle_sigterm` của app chỉ chạy sau cùng (uvicorn gọi lại handler cũ sau khi tắt xong). Phần dọn dẹp thật sự nằm trong `lifespan`.

## Part 2: Docker

_(CP2)_

## Part 3: Cloud Deployment

_(CP6)_

## Part 4: API Security

_(CP3)_

## Part 5: Scaling & Reliability

_(CP4)_

## Part 6: Final Project — output `check_production_ready.py`

_(CP5)_

## AI log

| Checkpoint | Công cụ | Dùng để làm gì | Mình tự kiểm tra lại thế nào |
|---|---|---|---|
| CP0–CP1 | Claude Code | Đọc đề, chạy bản develop và production, gom output curl và log, soạn bảng anti-pattern và bảng so sánh | Tự chạy lại các lệnh curl, đối chiếu số dòng trong bảng với code, kiểm tra hai lỗi logging và `.env` bằng cách chạy lại có và không có `OPENAI_API_KEY` |
