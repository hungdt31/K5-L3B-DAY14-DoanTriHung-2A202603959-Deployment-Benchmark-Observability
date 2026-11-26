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

### Exercise 2.1: Dockerfile questions (`02-docker/develop/Dockerfile`)

1. **Base image:** `python:3.11` (dòng 8). Đây là bản Python đầy đủ chạy trên Debian, có sẵn gcc, git và các header để build. Đo bằng `docker image inspect` được **1598.5 MB**, riêng base image đã chiếm khoảng 96% dung lượng image develop.
2. **Working directory:** `/app` (dòng 11). Các lệnh `COPY`, `RUN`, `CMD` phía sau đều chạy trong thư mục này. Nếu `/app` chưa có thì Docker tự tạo.
3. **Tại sao `COPY requirements.txt` trước:** mỗi lệnh trong Dockerfile tạo ra một layer. Khi file đầu vào của một lệnh `COPY` thay đổi, layer đó và **mọi layer phía sau** đều phải build lại. Code thì sửa liên tục, còn dependencies hiếm khi đổi. Vì vậy ta copy `requirements.txt` rồi chạy `pip install` trước, sau đó mới copy code. Như thế, sửa code chỉ làm build lại vài layer cuối, còn layer `pip install` (53.3 MB) vẫn lấy từ cache. Nếu `COPY . .` đứng trước `pip install` thì sửa một dòng code cũng phải cài lại toàn bộ dependencies.
   Mình đã thử trên một bản sao của thư mục develop:

   | Thay đổi | Layer `pip install` | Thời gian build |
   |---|---|---|
   | Sửa `app.py` | `CACHED` | **0.83 s** |
   | Sửa `requirements.txt` | chạy lại | **54.41 s** |
4. **CMD và ENTRYPOINT:**
   - `CMD` là lệnh **mặc định**: tham số truyền vào `docker run <image> <args>` sẽ **thay thế** toàn bộ CMD. Ví dụ `docker run --rm my-agent:develop sh -c 'whoami'` chạy `sh` thay cho `python app.py`. Mình đã dùng đúng cách này để kiểm tra user trong container.
   - `ENTRYPOINT` là chương trình **cố định**: tham số của `docker run` được **nối thêm** vào sau ENTRYPOINT chứ không thay nó. Muốn đổi ENTRYPOINT thì phải dùng `--entrypoint`.
   - Hai lệnh hay dùng kết hợp: `ENTRYPOINT ["uvicorn"]` + `CMD ["main:app", "--port", "8000"]`, khi đó CMD là tham số mặc định và có thể ghi đè.
   - Cả hai lệnh ở đây đều viết dạng exec (`["python", "app.py"]`), nên process Python chạy làm PID 1 và trực tiếp nhận SIGTERM từ `docker stop`. Nếu viết dạng shell (`CMD python app.py`) thì `/bin/sh` làm PID 1 và tín hiệu có thể không tới được app, nên không tắt graceful được.

### Exercise 2.2: Build và run bản develop

```bash
# từ gốc repo
docker build -f 02-docker/develop/Dockerfile -t my-agent:develop .
docker run -d --name cp2-develop -p 8000:8000 my-agent:develop
```

```text
$ docker ps
NAMES         IMAGE              STATUS                  PORTS
cp2-develop   my-agent:develop   Up Less than a second   0.0.0.0:8000->8000/tcp, [::]:8000->8000/tcp

$ curl -s -X POST "http://localhost:8000/ask?question=What%20is%20Docker%3F" -w '\nHTTP %{http_code}\n'
{"answer":"Container là cách đóng gói app để chạy ở mọi nơi. Build once, run anywhere!"}
HTTP 200

$ curl -s http://localhost:8000/health -w '\nHTTP %{http_code}\n'
{"status":"ok","uptime_seconds":0.5,"container":true}
HTTP 200

$ docker logs cp2-develop
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
INFO:     172.17.0.1:41450 - "POST /ask?question=What%20is%20Docker%3F HTTP/1.1" 200 OK
```

Lệnh curl gửi JSON body như trong `CODE_LAB.md` trả **422** vì bản develop nhận `question` qua query string (README, Lưu ý số 3).

Debug bằng `docker exec`: `docker exec cp2-develop whoami` in ra `root`, nghĩa là container develop chạy bằng root.

### Exercise 2.3: Multi-stage build và image size

```bash
docker build -f 02-docker/production/Dockerfile -t my-agent:advanced .
```

**Stage 1 (`builder`, `python:3.11-slim`):** cài công cụ build (`gcc`, `libpq-dev`) bằng apt, rồi `pip install --user` để dependencies nằm gọn trong `/root/.local`. Stage này chỉ để build, không được dùng để deploy.

**Stage 2 (`runtime`, `python:3.11-slim`):** bắt đầu lại từ một base slim sạch, chỉ `COPY --from=builder /root/.local` (40.8 MB packages) cùng `main.py` và `utils/`. Stage này tạo user `appuser` và chạy bằng user đó (`USER appuser`), có `HEALTHCHECK`, chạy `uvicorn --workers 2`.

**Kết quả đo** (`docker images` trên Docker 29 có hai cột: DISK USAGE là dung lượng sau giải nén, CONTENT SIZE là dung lượng nén khi push/pull):

```text
$ docker images my-agent
IMAGE               ID             DISK USAGE   CONTENT SIZE
my-agent:advanced   4d6ae612777b        240MB         57.4MB
my-agent:develop    f6a760ab2112       1.67GB          426MB

$ docker image inspect my-agent:develop my-agent:advanced --format '{{index .RepoTags 0}} {{.Size}}'
my-agent:develop 1667788274
my-agent:advanced 239815463
```

- Develop: **1667.8 MB** (nén: 426 MB)
- Production: **239.8 MB** (nén: 57.4 MB)
- Chênh lệch: **giảm 85.6%** (nhỏ hơn khoảng 7.0 lần); tính theo dung lượng nén thì giảm 86.5%

**Tại sao nhỏ hơn: dung lượng giảm ở đâu.** Mình đo từng phần bằng `docker image inspect` và `docker history`:

| Image | Dung lượng | Ghi chú |
|---|---:|---|
| `python:3.11` (base của develop) | 1598.5 MB | Debian đầy đủ + toolchain |
| `python:3.11-slim` (base của production) | 187.1 MB | Chỉ có Python runtime |
| Stage `builder` (`--target builder`) | 559.3 MB | slim + gcc + libpq-dev + apt + packages |
| Stage `runtime` (`my-agent:advanced`) | 239.8 MB | slim + 40.8 MB packages + code |

1. **Đổi base image `python:3.11` sang `-slim`** tiết kiệm khoảng 1.4 GB. Đây là phần giảm lớn nhất.
2. **Multi-stage:** gcc, libpq-dev và cache apt chỉ nằm ở stage builder (559 MB). Stage runtime không chứa chúng, nên bớt thêm khoảng **319 MB (57%)** so với việc dùng thẳng builder làm image chạy. Mình kiểm tra trong image production: `which gcc` báo `no gcc`, còn trong image develop có `/usr/bin/gcc`.
3. **Lợi ích ngoài dung lượng:** ít package thì ít lỗ hổng bảo mật (CVE) hơn, và kẻ tấn công vào được container cũng không có compiler để dùng. Container chạy bằng `appuser`, không phải root (`whoami` → `appuser`). Image nhỏ thì pull nhanh hơn, nên deploy và scale cũng nhanh hơn.

### Exercise 2.4: Docker Compose stack

```bash
cd 02-docker/production
touch .env.local          # compose bắt buộc có file này (README, Lưu ý số 5), đã nằm trong .gitignore
docker compose -p k5-day14-docker up -d --build
```

**Các service được start:**

```text
$ docker compose -p k5-day14-docker ps
SERVICE   STATUS                    PORTS
agent     Up 27 seconds (healthy)   8000/tcp
nginx     Up 26 seconds             0.0.0.0:80->80/tcp, [::]:80->80/tcp, 0.0.0.0:443->443/tcp, [::]:443->443/tcp
qdrant    Up 38 seconds (healthy)   6333-6334/tcp
redis     Up 38 seconds (healthy)   6379/tcp
```

Thứ tự khởi động theo `depends_on`: `redis` và `qdrant` chạy trước, Compose đợi cả hai `Healthy` (`condition: service_healthy`), rồi mới start `agent`, sau đó tới `nginx`.

**Architecture diagram:**

```text
                         Host (laptop)
                              │  :80 (HTTP)  :443 (HTTPS, chưa có cert)
 ┌────────────────────────────┼──────────────────────────────────────────┐
 │  network "internal"        ▼                     (bridge, 172.20.0.0/16)
 │                    ┌───────────────┐
 │                    │     nginx     │  reverse proxy + load balancer
 │                    │  nginx:alpine │  rate limit 10 r/s/IP, burst 20 → 429
 │                    └───────┬───────┘  thêm security headers
 │                            │ proxy_pass http://agent:8000  (upstream agent_backend)
 │                            ▼
 │                    ┌───────────────┐
 │                    │     agent     │  FastAPI, uvicorn 2 workers, user appuser
 │                    │ (Dockerfile   │  không publish port ra host
 │                    │  target       │  healthcheck GET /health
 │                    │  runtime)     │
 │                    └───┬───────┬───┘
 │   REDIS_URL=           │       │  QDRANT_URL=
 │   redis://redis:6379/0 │       │  http://qdrant:6333
 │                        ▼       ▼
 │          ┌───────────────┐   ┌───────────────┐
 │          │     redis     │   │    qdrant     │
 │          │ redis:7-alpine│   │ qdrant v1.9.0 │
 │          │ session, rate │   │ vector DB     │
 │          │ limit (LRU    │   │ cho RAG       │
 │          │ 256 MB)       │   │               │
 │          └───────┬───────┘   └───────┬───────┘
 │            volume redis_data   volume qdrant_data
 └───────────────────────────────────────────────────────────────────────┘
```

**Các service giao tiếp với nhau thế nào:**
- Cả 4 service cùng nằm trên network bridge `internal` và gọi nhau **bằng tên service**, nhờ DNS nội bộ của Docker. Kiểm tra từ trong container agent: `redis → 172.20.0.3`, `qdrant → 172.20.0.2`, `nginx → 172.20.0.5`.
- Chỉ **nginx** publish port ra host (80/443). Gọi thẳng `curl localhost:8000/health` từ host thì không kết nối được (`000`): agent, redis và qdrant chỉ truy cập được từ bên trong network, đây là cách giảm bề mặt tấn công.
- Client gọi Nginx, Nginx `proxy_pass` tới `agent:8000`, và agent đọc địa chỉ Redis, Qdrant qua env `REDIS_URL`, `QDRANT_URL`. Lưu ý: `main.py` của bài mẫu chưa thật sự kết nối Redis hay Qdrant (trong code không có lời gọi nào), nên hai service này hiện mới đứng sẵn trong kiến trúc.

**Test qua Nginx:**

```text
$ curl -si http://localhost/health
HTTP/1.1 200 OK
Server: nginx
Content-Type: application/json
X-Frame-Options: DENY
X-Content-Type-Options: nosniff
X-XSS-Protection: 1; mode=block

{"status":"ok","uptime_seconds":6.2,"version":"2.0.0","timestamp":"2026-11-26T08:08:00.802174"}

$ curl -si -X POST http://localhost/ask -H "Content-Type: application/json" -d '{"question": "Explain microservices"}'
HTTP/1.1 200 OK
Server: nginx
Content-Type: application/json
X-Frame-Options: DENY
X-Content-Type-Options: nosniff
X-XSS-Protection: 1; mode=block

{"answer":"Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận."}
```

Header `Server: nginx` (đã ẩn version nhờ `server_tokens off`) và các security header cho thấy request đi qua Nginx. Log của agent ghi IP nguồn là `172.20.0.5`, chính là container nginx.

**Rate limit của Nginx:** gửi 40 request **tuần tự** thì cả 40 đều trả 200, vì mock LLM mất khoảng 150 ms mỗi câu nên tốc độ chưa vượt 10 r/s. Gửi 60 request **song song** (`xargs -P 60`) thì có **23 × 200 và 37 × 429**, body trả về là `{"error":"Too many requests","retry_after":1}`. Con số 23 khớp cấu hình: 1 request + `burst=20` được cho qua ngay (`nodelay`), cộng thêm vài request khi bucket kịp nạp lại.

Dọn stack: `docker compose -p k5-day14-docker down`.

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
| CP2 | Claude Code | Build và chạy image, đo dung lượng, chạy stack Compose, thử rate limit của Nginx và layer cache, soạn câu trả lời và sơ đồ | Số MB lấy từ `docker images` và `docker image inspect` chạy trên máy mình, không chép từ README. Tự chạy lại lệnh curl qua Nginx và vòng lặp song song để thấy 429 |
