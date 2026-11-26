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

> Theo `CHECKPOINTS.md` (CP6), lớp K5 deploy luôn **agent Part 6** (`06-lab-complete/`), không deploy app mẫu trong `03-cloud-deployment/`. Chi tiết xem [DEPLOYMENT.md](DEPLOYMENT.md).

### Exercise 3.1: Railway deployment (nền tảng chính)

- **URL:** https://agent-production-51bf7a38.up.railway.app
- **Project:** `day14-agent`, gồm service `agent` (build từ `06-lab-complete/Dockerfile`) và service `Redis` (Railway Redis, có volume).
- **Biến môi trường** đặt trên Railway: `AGENT_API_KEY` (truyền qua stdin), `REDIS_URL=${{Redis.REDIS_URL}}`, `ENVIRONMENT=production`, `RATE_LIMIT_PER_MINUTE=10`, `DAILY_BUDGET_USD=1.0`, `LOG_LEVEL=INFO`. `PORT` do Railway tự cấp (8080).
- **Screenshots:** [dashboard](screenshots/dashboard.png) · [running](screenshots/running.png) · [test](screenshots/test.png)

4 lệnh tự kiểm của CP6 (`BASE` là URL trên, `AGENT_API_KEY` là key đặt trên Railway):

```text
$ curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/health"
200
$ curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/ready"
200
$ curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -d '{"question":"Hello"}'
401
$ curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -H "X-API-Key: $AGENT_API_KEY" -d '{"question":"Hello"}'
200
```

Rate limit dùng Redis trên cloud cũng chạy (8 request 200 rồi 429 kèm `retry-after`), và `python check_production_ready.py --public-url <URL>` báo **PASS 12/12 (44/44 checks)**.

### Exercise 3.2: So sánh `railway.toml` và `render.yaml`

So sánh hai file mình viết cho agent Part 6 (`06-lab-complete/`), có đối chiếu với hai file mẫu trong `03-cloud-deployment/`:

| Khía cạnh | `railway.toml` | `render.yaml` | Vì sao khác |
|---|---|---|---|
| **Phạm vi** | Config cho **một service**. Redis và biến môi trường tạo riêng (CLI hoặc dashboard) | **Blueprint** mô tả **cả hạ tầng**: web service + Key Value (Redis) + biến môi trường + liên kết giữa chúng | Railway quản lý project bằng CLI/UI, file chỉ chỉnh build/deploy. Render theo hướng Infrastructure as Code: commit file là Render dựng lại đúng toàn bộ stack |
| **Định dạng** | TOML: `[build]`, `[deploy]` | YAML: danh sách `services:` | |
| **Build** | `builder = "DOCKERFILE"` + `dockerfilePath` | `runtime: docker` + `dockerfilePath` / `dockerContext` (+ `rootDir: 06-lab-complete` vì file nằm trong thư mục con) | Bản mẫu trong `03-cloud-deployment/` dùng `NIXPACKS` / `runtime: python` kèm `buildCommand`, để platform tự đoán cách build. Bản Part 6 dùng Dockerfile nên image chạy local, trong CI và trên cloud là **một** |
| **Lệnh chạy** | Không có `startCommand`, dùng `CMD` của Dockerfile | Không có `startCommand`, dùng `CMD` của Dockerfile | Cả hai platform đều cấp `PORT` qua env. `CMD ... --port ${PORT:-8000}` chạy được ở cả hai mà không phải sửa |
| **Health check** | `healthcheckPath = "/health"`, `healthcheckTimeout = 60` | `healthCheckPath: /health` | Giống nhau về ý nghĩa: deploy mới chỉ nhận traffic khi `/health` trả 200 (zero-downtime) |
| **Restart** | `restartPolicyType = "ON_FAILURE"`, tối đa 3 lần | Render tự restart, không cấu hình được trong file | |
| **Secret** | **Không nằm trong file.** Đặt bằng `railway variable set ... --stdin` | Khai báo **tên** trong file nhưng không có giá trị: `generateValue: true` (Render tự sinh `AGENT_API_KEY`) hoặc `sync: false` (nhập tay trên dashboard) | Không bao giờ commit giá trị secret. Render cần biết biến tồn tại để dựng Blueprint |
| **Redis** | Service riêng; agent tham chiếu bằng `REDIS_URL=${{Redis.REDIS_URL}}` | `type: keyvalue` ngay trong file; agent lấy URL bằng `fromService: {type: keyvalue, property: connectionString}` | Cả hai đều dùng **tham chiếu** thay vì chép URL hay mật khẩu: đổi mật khẩu Redis thì agent tự nhận giá trị mới. File mẫu còn dùng `type: redis`, tên cũ của Render |
| **Deploy** | `railway up` upload thư mục từ máy, không cần GitHub | Phải push repo lên GitHub, rồi Render Blueprint đọc file từ repo (`autoDeploy` mỗi lần push) | Railway hợp làm nhanh từ local. Render gắn với Git, hợp với luồng review qua PR |
| **Ghi chú** | Railway CLI 5.75 cảnh báo "Config as Code" (`railway.toml`) sẽ bị **deprecated sau 2026-12-01**, khuyến nghị chuyển sang `.railway/railway.ts` | Free plan: service ngủ khi không có traffic (lần gọi đầu mất 30–60 s), Key Value free không lưu xuống đĩa | |

### Exercise 3.3: Pipeline CI/CD trên Cloud Run (đọc `cloudbuild.yaml` + `service.yaml`, không deploy)

Mỗi lần push lên `main`, **Cloud Build** chạy 4 bước tuần tự (nối với nhau bằng `waitFor`):

1. **`test`:** chạy `pytest tests/` trong container `python:3.11-slim`. Test fail thì pipeline dừng, không build.
2. **`build`:** build Docker image, gắn 2 tag `gcr.io/$PROJECT_ID/ai-agent:$COMMIT_SHA` và `:latest`; dùng `--cache-from :latest` để tận dụng layer cache.
3. **`push`:** đẩy cả 2 tag lên Container Registry.
4. **`deploy`:** `gcloud run deploy` image theo **`$COMMIT_SHA`** (không dùng `latest`, nên biết chính xác commit nào đang chạy và rollback được) lên `asia-southeast1`. Cấu hình: public (`--allow-unauthenticated`), từ 1 đến 10 instance (`min-instances=1` để tránh cold start), 1 CPU / 512 MiB; secret lấy từ **Secret Manager** (`--set-secrets`), không ghi trong file.

`service.yaml` mô tả cùng service đó dưới dạng khai báo (Knative), áp dụng bằng `gcloud run services replace`: autoscale 1–10 instance, mỗi instance xử lý tối đa 80 request đồng thời, liveness probe `/health`, startup probe `/ready`, secret qua `secretKeyRef`.

Nhận xét khi đối chiếu với repo:
- Repo **không có thư mục `tests/`**, nên bước 1 sẽ fail ngay.
- `gcr.io` (Container Registry) đã ngừng hỗ trợ, nên chuyển sang Artifact Registry (`REGION-docker.pkg.dev/...`).
- `service.yaml` tự đặt `env PORT=8000`, nhưng Cloud Run **giữ riêng** biến `PORT` và tự cấp (mặc định 8080), nên phải bỏ biến này và chỉ khai báo `containerPort`.

## Part 4: API Security

### Exercise 4.1: API Key authentication (`04-api-gateway/develop/`)

Chạy bằng `AGENT_API_KEY=secret-key-123 python app.py`.

**API key được kiểm tra ở đâu?** Ở dependency `verify_api_key` (`develop/app.py:39-54`). `APIKeyHeader(name="X-API-Key", auto_error=False)` (dòng 36) lấy header ra. Dependency được gắn vào endpoint bằng `Depends(verify_api_key)` (dòng 70), nên `/ask` chỉ chạy khi key hợp lệ. `/` và `/health` không gắn dependency, nên vẫn public; platform cần gọi `/health` mà không có key.

**Sai key thì sao?** Thiếu header thì trả **401** `Missing API key`. Có header nhưng sai key thì trả **403** `Invalid API key.` (dòng 44–53). Request bị chặn ngay trước khi gọi LLM, nên không tốn tiền. (Hợp đồng của Part 6 yêu cầu thiếu hoặc sai key đều trả 401, nên tới bài đó phải đổi lại.)

**Rotate key thế nào?** Key được đọc từ env `AGENT_API_KEY` lúc khởi động (dòng 35). Muốn rotate thì đổi biến môi trường trên platform rồi restart hoặc redeploy, không phải sửa code. Nhưng app chỉ nhận **một** key, nên vừa đổi xong là mọi client đang dùng key cũ bị lỗi ngay. Muốn rotate không gián đoạn thì cho app nhận nhiều key cùng lúc (ví dụ `AGENT_API_KEYS=new,old`), phát key mới cho client, rồi mới bỏ key cũ.

**Điểm yếu thấy được trong code:**
- Có key mặc định `"demo-key-change-in-production"`: quên đặt env thì app vẫn chạy với một key ai cũng biết. Nên dừng app ngay khi thiếu key.
- Lúc khởi động, app **in key ra log** (dòng 91–92; log server có dòng `API Key: secret-key-123`).
- So sánh bằng `!=` không chạy trong thời gian cố định, nên về lý thuyết có thể bị tấn công timing. Nên dùng `hmac.compare_digest`.

```text
$ curl -i -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question": "Hello"}'
HTTP/1.1 401 Unauthorized
server: uvicorn
content-type: application/json

{"detail":"Missing API key. Include header: X-API-Key: <your-key>"}

$ curl -i -X POST http://localhost:8000/ask -H "X-API-Key: wrong-key" -H "Content-Type: application/json" -d '{"question": "Hello"}'
HTTP/1.1 403 Forbidden
server: uvicorn
content-type: application/json

{"detail":"Invalid API key."}

$ curl -i -X POST http://localhost:8000/ask -H "X-API-Key: secret-key-123" -H "Content-Type: application/json" -d '{"question": "Hello"}'
HTTP/1.1 200 OK
server: uvicorn
content-type: application/json

{"question":"Hello","answer":"Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận."}
```

(Có key nhưng `question` rỗng thì trả 422 `question is required`; `/health` không cần key, trả 200.)

### Exercise 4.2: JWT authentication (`04-api-gateway/production/`)

**Luồng JWT** (`auth.py`):
1. `POST /auth/token` với `{"username","password"}`. `authenticate_user` kiểm tra trong `DEMO_USERS`; sai thì trả **401** `Invalid credentials`.
2. `create_token` tạo payload `{sub, role, iat, exp = now + 60 phút}`, rồi **ký HS256** bằng `JWT_SECRET`.
3. Với mỗi request, client gửi `Authorization: Bearer <token>`. `verify_token` gọi `jwt.decode(...)` để kiểm tra chữ ký và `exp`, rồi trả `{username, role}` cho endpoint.
4. Server **không phải tra DB hay session** ở mỗi request vì mọi thông tin đã nằm trong token (stateless). Nhờ vậy instance nào cũng xác thực được, chỉ cần dùng chung secret.

| Trường hợp | Kết quả |
|---|---|
| Không gửi token | **401** + header `WWW-Authenticate: Bearer` |
| Token bị sửa (chữ ký sai) | **403** `Invalid token.` |
| Token hết hạn | **401** `Token expired` |
| Token hợp lệ | **200** |

**Payload chỉ được encode base64, không được mã hoá.** Mình decode phần giữa của token mà không cần secret và đọc được ngay:

```text
{'alg': 'HS256', 'typ': 'JWT'}
{'sub': 'student', 'role': 'user', 'iat': 1795682357, 'exp': 1795685957}
```

Vì vậy không được để dữ liệu nhạy cảm trong payload. Chữ ký chỉ chống *sửa* nội dung, không chống *đọc*.

```text
$ curl -i -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question":"Explain JWT"}'
HTTP/1.1 401 Unauthorized
www-authenticate: Bearer
x-content-type-options: nosniff
x-frame-options: DENY
referrer-policy: strict-origin-when-cross-origin

{"detail":"Authentication required. Include: Authorization: Bearer <token>"}

$ curl -s -X POST http://localhost:8000/auth/token -H "Content-Type: application/json" -d '{"username":"student","password":"demo123"}'
{"access_token":"eyJhbGciOi***MASKED***","token_type":"bearer","expires_in_minutes":60,"hint":"Include in header: Authorization: Bearer eyJ***"}

$ curl -i -X POST http://localhost:8000/ask -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"question":"Explain JWT"}'
HTTP/1.1 200 OK
x-content-type-options: nosniff
x-frame-options: DENY
referrer-policy: strict-origin-when-cross-origin

{"question":"Explain JWT","answer":"Đây là câu trả lời từ AI agent (mock). Trong production, đây sẽ là response từ OpenAI/Anthropic.","usage":{"requests_remaining":9,"budget_remaining_usd":2.1e-05}}

$ curl -i -X POST http://localhost:8000/ask -H "Authorization: Bearer <token đã sửa 1 ký tự cuối>" -H "Content-Type: application/json" -d '{"question":"hi"}'
HTTP/1.1 403 Forbidden

{"detail":"Invalid token."}
```

Input validation bằng Pydantic (`AskRequest`: `min_length=1`, `max_length=1000`): `question` rỗng trả **422**, dài 1001 ký tự cũng trả **422**.

**Điểm yếu:**
- Có `JWT_SECRET` mặc định nằm trong code: ai biết chuỗi này là tự ký được token với `role=admin`.
- Mật khẩu lưu dạng plaintext trong `DEMO_USERS`.
- Token không thu hồi được trước khi hết hạn (cần thêm danh sách token bị chặn, hoặc dùng access token ngắn hạn kèm refresh token).

### Exercise 4.3: Rate limiting

**Thuật toán:** **Sliding window** (`rate_limiter.py`). Mỗi user có một `deque` lưu timestamp của từng request. Mỗi lần kiểm tra, timestamp cũ hơn `now - 60s` bị bỏ đi; nếu số còn lại `>= max_requests` thì trả 429. Docstring ghi là "Sliding Window Counter", nhưng thực chất code lưu từng timestamp nên đúng hơn là *sliding window log*. Cách này đếm chính xác (không bị dồn request ở ranh giới như fixed window), đổi lại tốn bộ nhớ O(limit) cho mỗi user.

**Limit:** `rate_limiter_user` = **10 request / 60 giây** cho role `user` (`student`), `rate_limiter_admin` = **100 request / 60 giây** cho role `admin` (`teacher`).

**Bypass cho admin:** `app.py:141` chọn limiter theo `role` lấy từ JWT: `rate_limiter_admin if role == "admin" else rate_limiter_user`. Admin không được bỏ qua hoàn toàn mà được lên **tier cao hơn** (100 req/phút). Mình kiểm tra bằng `teacher`: 15 request liên tiếp đều trả 200. Muốn bỏ qua hẳn thì không gọi `limiter.check()` khi `role == "admin"`. Role nằm trong token có chữ ký, nên user thường không tự nâng quyền được (trừ khi lộ `JWT_SECRET`).

**Test:** vòng lặp 20 request bằng token `student` của mình:

```text
[1] HTTP 200  requests_remaining=9
[2] HTTP 200  requests_remaining=8
[3] HTTP 200  requests_remaining=7
[4] HTTP 200  requests_remaining=6
[5] HTTP 200  requests_remaining=5
[6] HTTP 200  requests_remaining=4
[7] HTTP 200  requests_remaining=3
[8] HTTP 200  requests_remaining=2
[9] HTTP 200  requests_remaining=1
[10] HTTP 200  requests_remaining=0
[11] HTTP 429  {"detail":{"error":"Rate limit exceeded","limit":10,"window_seconds":60,"retry_after_seconds":58}}
...
[20] HTTP 429  {"detail":{"error":"Rate limit exceeded","limit":10,"window_seconds":60,"retry_after_seconds":58}}
```

Response 429 đầy đủ:

```text
$ curl -i -X POST http://localhost:8000/ask -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"question":"Test 21"}'
HTTP/1.1 429 Too Many Requests
x-ratelimit-limit: 10
x-ratelimit-remaining: 0
x-ratelimit-reset: 1795682421
retry-after: 56
content-type: application/json

{"detail":{"error":"Rate limit exceeded","limit":10,"window_seconds":60,"retry_after_seconds":56}}
```

Header `Retry-After` cho client biết sau bao lâu thì được gọi lại.

**Hạn chế:** state nằm trong RAM của process. Chạy 3 instance thì mỗi user thực tế được 30 req/phút, và restart là bộ đếm về 0. Bản production phải lưu bộ đếm trong Redis (ví dụ dùng sorted set với timestamp làm score), đây cũng là yêu cầu của Part 6.

### Exercise 4.4: Cost guard

#### Code có sẵn làm gì (`production/cost_guard.py`)
- **Tính tiền:** theo token, với giá GPT-4o-mini ($0.15/1M input, $0.60/1M output). Bản mock ước lượng token bằng `số từ × 2`.
- **Hai lớp budget theo ngày:** **$1/ngày mỗi user**, vượt thì trả **402 Payment Required**; và **$10/ngày cho toàn hệ thống**, vượt thì trả **503**. Dùng tới 80% budget thì ghi log cảnh báo.
- **Thứ tự trong `/ask`:** xác thực JWT, rồi rate limit, rồi `check_budget` (**trước** khi gọi LLM), rồi gọi LLM, cuối cùng `record_usage` (**sau** khi có số token thật).
- **Reset:** dựa vào chuỗi ngày `YYYY-MM-DD`. Sang ngày mới thì record cũ bị thay bằng record mới.

Mình cho class `CostGuard` có sẵn chạy với budget rất nhỏ để thấy 402 và 503 (gọi trực tiếp class, không sửa code):

```text
[1] OK   used=$0.000019
...
[5] OK   used=$0.000093
[6] OK   used=$0.000112        ← vẫn được cho qua dù sau request này đã vượt budget $0.0001
[7] HTTP 402 {'error': 'Daily budget exceeded', 'used_usd': 0.000112, 'budget_usd': 0.0001, 'resets_at': 'midnight UTC'}
global: user4 -> HTTP 503 Service temporarily unavailable due to budget limits. Try again tomorrow.
```

**Vấn đề của bản có sẵn:**
1. **Lưu trong RAM:** restart là mất số liệu chi tiêu. Chạy N instance thì mỗi instance giữ một budget riêng, nên budget thực tế thành **N lần**.
2. **Có thể vượt budget:** code chỉ so `đã tiêu >= budget` mà không cộng **chi phí ước tính** của request sắp chạy, nên request cuối có thể đẩy tổng chi tiêu vượt budget (dòng [6] ở trên).
3. **Không atomic:** giữa lúc `check_budget` và `record_usage` có cả một lần gọi LLM. Nhiều request song song đều thấy "còn budget" và cùng được cho qua.
4. **Lỗi nhỏ:**
   - `/ask` trả `"budget_remaining_usd": usage.total_cost_usd`, tức là số tiền **đã tiêu**, không phải số còn lại (`app.py:160`).
   - Response 402 ghi `resets_at: midnight UTC`, nhưng `time.strftime` dùng giờ địa phương (GMT+7), nên thực tế reset lúc 17:00 UTC.

#### Cách làm theo đề ($10/tháng, lưu trong Redis, reset đầu tháng)

```python
from datetime import datetime, timezone
import redis

MONTHLY_BUDGET_USD = 10.0

# Check + cộng tiền trong MỘT thao tác atomic: Redis chạy Lua script tuần tự,
# nên 2 request song song (kể cả ở 2 instance khác nhau) không thể cùng lọt qua.
_RESERVE = """
local current = tonumber(redis.call('GET', KEYS[1]) or '0')
local cost    = tonumber(ARGV[1])
local budget  = tonumber(ARGV[2])
if current + cost > budget then
  return 0
end
redis.call('INCRBYFLOAT', KEYS[1], cost)
redis.call('EXPIREAT', KEYS[1], ARGV[3])
return 1
"""

def _month_key(user_id, now):          # key mới mỗi tháng → tự "reset" đầu tháng
    return f"budget:{user_id}:{now:%Y-%m}"

def _next_month_epoch(now):            # key tự xoá sau khi hết tháng (+1 ngày dự phòng)
    y, m = (now.year + 1, 1) if now.month == 12 else (now.year, now.month + 1)
    return int(datetime(y, m, 1, tzinfo=timezone.utc).timestamp()) + 86400

class RedisCostGuard:
    def __init__(self, r: redis.Redis, budget: float = MONTHLY_BUDGET_USD):
        self.r, self.budget = r, budget
        self._reserve = r.register_script(_RESERVE)

    def check_budget(self, user_id: str, estimated_cost: float, now=None) -> bool:
        now = now or datetime.now(timezone.utc)
        return self._reserve(keys=[_month_key(user_id, now)],
                             args=[estimated_cost, self.budget, _next_month_epoch(now)]) == 1

    def adjust(self, user_id: str, delta: float, now=None) -> None:
        """Sau khi LLM trả về: cộng/trừ chênh lệch giữa chi phí thật và chi phí ước tính."""
        now = now or datetime.now(timezone.utc)
        self.r.incrbyfloat(_month_key(user_id, now), delta)
```

**Các quyết định thiết kế:**
- **Key theo tháng (`budget:<user>:2026-11`):** sang tháng mới thì key mới bắt đầu từ 0, nên tự "reset" mà không cần cron. `EXPIREAT` dọn key cũ để Redis không đầy dần.
- **Giữ chỗ trước, trừ sau:** trước khi gọi LLM, giữ chỗ bằng *chi phí ước tính* (`len(prompt)` + `max_tokens`). Sau khi có số token thật thì `adjust` phần chênh lệch. Cách này giải quyết vấn đề 2.
- **Kiểm tra và cộng tiền atomic bằng Lua:** giải quyết vấn đề 3. Lời giải mẫu trong `CODE_LAB.md` làm `GET`, so sánh, rồi mới `INCRBYFLOAT` thành nhiều lệnh riêng, nên có race condition.
- **Lưu trong Redis dùng chung:** mọi instance thấy cùng một con số, restart không mất. Giải quyết vấn đề 1.
- **Dùng UTC:** không phụ thuộc múi giờ của server.

**Đã test với Redis thật** (`redis:7-alpine` chạy trong container, script ở scratchpad):

```text
1) Tiêu dần $3/lần:
   [1] allowed=True  spent=$3.00
   [2] allowed=True  spent=$6.00
   [3] allowed=True  spent=$9.00
   [4] allowed=False  spent=$9.00          ← 9 + 3 > 10 nên bị chặn, chi tiêu không vượt budget
2) Còn $1 → request $0.99 vẫn qua, $0.02 tiếp theo bị chặn: True False spent=$9.99
3) Key=budget:student:2026-11, TTL tới ~2026-12-02 00:00 UTC
4) Sang tháng 12: allowed=True spent_dec=$3.00 ← budget đã reset
5) User khác độc lập: True
6) 50 request song song x $1, budget $10 → allowed=10, spent=$10.00
```

So sánh với **lời giải mẫu trong `CODE_LAB.md`**, cũng 50 request song song × $1 (có thêm 5 ms trễ giữa `GET` và `INCRBYFLOAT` để mô phỏng độ trễ mạng tới Redis trên cloud):

```text
run 1: allowed=34, spent=$34.00 (budget $10)
run 2: allowed=44, spent=$44.00 (budget $10)
run 3: allowed=46, spent=$46.00 (budget $10)
```

Như vậy lời giải mẫu để lọt tới **4.6 lần budget** khi nhiều request đến cùng lúc, còn bản dùng Lua chặn đúng ở $10.

#### Vì sao cần cả rate limit lẫn cost guard
- **Rate limit bảo vệ tốc độ** (chống spam, DoS, giữ cho server không quá tải). **Cost guard bảo vệ tiền.**
- Với mock, mỗi request tốn trung bình khoảng $0.0000181 (`/me/usage`: 10 request = $0.000181). Rate limit 10 req/phút cho tối đa 14,400 request/ngày, tức khoảng **$0.26/ngày**, chưa bao giờ chạm budget $1. Ở đây rate limit đã đủ chặn chi phí.
- Nhưng với LLM thật và prompt dài (ví dụ khoảng $0.01/request với RAG context dài, model lớn), cùng 14,400 request/ngày đó thành **$144/ngày**, mà mỗi request vẫn hợp lệ theo rate limit. Rate limit không biết **mỗi request đắt bao nhiêu**, nên phải có cost guard đặt trần tiền theo user và toàn hệ thống.

## Part 5: Scaling & Reliability

### Exercise 5.1: Health checks (`05-scaling-reliability/develop/`)

`develop/app.py` đã có sẵn hai endpoint:

- **`GET /health` (liveness, dòng 104–144):** trả 200 khi process còn sống, kèm `uptime`, `version` và phần kiểm tra bộ nhớ (`psutil`: dùng trên 90% RAM thì báo `degraded`). Endpoint này **không** kiểm tra dependency bên ngoài: Redis hay DB chết thì restart container cũng không giải quyết được gì.
- **`GET /ready` (readiness, dòng 147–168):** trả 503 khi `_is_ready = False`, tức là đang khởi động hoặc đang tắt; ngược lại trả 200 kèm số request đang xử lý.

```text
$ curl -i localhost:8000/health
HTTP/1.1 200 OK
content-type: application/json

{"status":"ok","uptime_seconds":0.4,"version":"1.0.0","environment":"development","timestamp":"2026-11-26T08:56:35.849965+00:00","checks":{"memory":{"status":"ok","used_percent":48.3}}}

$ curl -i localhost:8000/ready
HTTP/1.1 200 OK
content-type: application/json

{"ready":true,"in_flight_requests":1}
```

(`in_flight_requests` là 1 vì middleware đếm luôn chính request `/ready` này.)

**Liveness và readiness khác nhau ở đâu:**

| | `/health` (liveness) | `/ready` (readiness) |
|---|---|---|
| Câu hỏi | Process còn sống không? | Có nhận traffic được không? |
| Fail thì sao | Platform **restart** container | Load balancer **ngừng gửi traffic** vào, nhưng **không** restart |
| Kiểm tra gì | Chỉ trạng thái của chính process | Dependency (Redis, DB), đang khởi động hay đang tắt |
| Ví dụ | Process bị deadlock: restart là đúng | Redis chết: restart agent cũng vô ích, chỉ cần rút instance khỏi load balancer chờ Redis lên lại |

Nếu gộp chung hai endpoint, chẳng hạn cho `/health` kiểm tra Redis, thì một lần Redis chết sẽ làm platform **restart toàn bộ** agent cùng lúc (restart storm), dù agent không có lỗi gì.

**Vấn đề phát hiện khi chạy:** gọi `/health` trong lúc đang có một request `/ask` chạy thì mất **2.52 s** mới có phản hồi:

```text
/health latency = 2.518902s (HTTP 200)
```

Nguyên nhân: `ask_agent` khai báo là `async def` nhưng bên trong mock LLM gọi `time.sleep(3)`, một lệnh **blocking**, nên nó chặn cả event loop. Trong lúc đó mọi request khác, kể cả health check, đều phải đợi. Trên production, probe có timeout ngắn (compose của Part 5 đặt `timeout: 5s`, K8s mặc định 1 s), nên agent có thể bị coi là "chết" và bị restart oan. Cách sửa: khai báo endpoint là `def` thường để FastAPI chạy nó trong threadpool, hoặc dùng client LLM bất đồng bộ (`await`).

### Exercise 5.2: Graceful shutdown

**Code có sẵn làm gì:**
- `uvicorn.run(..., timeout_graceful_shutdown=30)` (dòng 198): khi nhận SIGTERM, uvicorn **ngừng nhận kết nối mới**, đợi request đang chạy xong (tối đa 30 s), rồi mới chạy phần shutdown của `lifespan`.
- Phần shutdown của `lifespan` (dòng 54–66): đặt `_is_ready = False`, đợi `_in_flight_requests` về 0 (middleware ở dòng 72–81 đếm số này), rồi dọn dẹp.
- `handle_sigterm` (dòng 175–187): chỉ ghi log. Uvicorn cài signal handler riêng khi khởi động nên ghi đè handler này. Log bên dưới cho thấy dòng `Received signal 15` xuất hiện **sau cùng**, vì uvicorn chỉ gọi lại handler cũ sau khi đã tắt xong.

**Test:** gửi `/ask` (mất 3 s), 0.8 s sau thì `kill -TERM`, rồi gửi thêm một request mới:

```text
[15:59:57.481] gửi /ask (Long task)
[15:59:58.282] kill -TERM 450890
[15:59:58.322] request MỚI sau SIGTERM: HTTP 000 exit=56        ← bị từ chối, không vào nữa
[16:00:00.550] request in-flight xong: {"answer":"Đây là câu trả lời từ AI agent (mock)..."} -> HTTP 200
[16:00:00.896] process đã thoát
```

```text
INFO:     127.0.0.1:60972 - "POST /ask?question=Long%20task HTTP/1.1" 200 OK
INFO:     Shutting down
INFO:     127.0.0.1:60994 - "POST /ask?question=Long%20task HTTP/1.1" 200 OK
INFO:     Waiting for application shutdown.
2026-11-26 16:00:00,639 INFO 🔄 Graceful shutdown initiated...
2026-11-26 16:00:00,639 INFO ✅ Shutdown complete
INFO:     Application shutdown complete.
INFO:     Finished server process [450890]
2026-11-26 16:00:00,640 INFO Received signal 15 — uvicorn will handle graceful shutdown
```

Request đang chạy **hoàn thành với 200** khoảng 2.3 s sau SIGTERM, request mới bị từ chối, rồi process mới thoát.

**Đối chứng bằng `kill -KILL`** (SIGKILL không bắt được, giống trường hợp container bị OOM-kill hoặc hết grace period):

```text
 -> HTTP 000  curl exit=52      ← "Empty reply from server": request đang chạy bị mất
```

**Nhận xét:** `_is_ready` chỉ chuyển sang `False` khi `lifespan` shutdown chạy, tức là **sau khi** mọi request đã xong. Vì vậy trong suốt thời gian chờ request cũ xong, `/ready` không kịp trả 503 để load balancer rút instance ra trước. Cách làm đúng là chuyển `_is_ready = False` **ngay khi nhận SIGTERM** (đây là yêu cầu của Part 6), đợi load balancer kịp nhận ra, rồi mới đợi request cũ xong và dọn dẹp kết nối.

### Exercise 5.3: Stateless design (`05-scaling-reliability/production/`)

**Vì sao state trong RAM hỏng khi scale:** mỗi instance là một process riêng với RAM riêng. Nếu lưu `conversation_history = {}` trong RAM:
- Câu 1 của user vào instance A, câu 2 được load balancer chuyển sang instance B, thì B không có history: agent "quên" cuộc hội thoại.
- Restart, deploy hoặc scale down là mất sạch history.
- Cách lách bằng sticky session (luôn gửi một user về cùng một instance) làm tải lệch giữa các instance, và instance đó chết thì vẫn mất dữ liệu.

**Code production đã chuyển state sang Redis như thế nào** (`production/app.py`):
- `save_session` / `load_session` (dòng 59–73) lưu session thành **một JSON blob** ở key `session:<id>`, dùng `SETEX` với TTL 3600 s, nên session bỏ dở tự hết hạn.
- `append_to_history` (dòng 76–90) đọc session, thêm message, giữ tối đa 20 message, rồi ghi lại.
- Agent không giữ gì trong RAM ngoài `INSTANCE_ID`, nên **instance nào cũng phục vụ được bất kỳ session nào**.

Kiểm tra trực tiếp trong Redis của stack:

```text
số session key: 18
ví dụ key: session:d7e2bee5-24bc-4a6a-a04e-80328f5093ae
TTL (giây): 3523
TYPE: string
{"history": [{"role": "user", "content": "Request 9", "timestamp": "2026-11-26T09:03:43.316747+00:00"}, ...
```

**Điểm yếu thấy được:**
1. **Fallback âm thầm về RAM** (dòng 42–45): không kết nối được Redis lúc khởi động thì app chỉ `print` cảnh báo rồi dùng dict trong RAM, tức là quay lại đúng anti-pattern mà vẫn chạy như bình thường. Mình đã kiểm tra: response có `"storage": "redis"` và log cả 3 instance đều ghi `Storage: Redis ✅`. Trên production nên **dừng app ngay** khi thiếu Redis, không nên âm thầm chuyển sang RAM.
2. **Lost update khi request song song:** `append_to_history` làm đọc, sửa, ghi trên một JSON blob mà không có lock. Hai request cùng session đến song song thì request ghi sau đè mất phần của request ghi trước. Mình gửi 8 request song song vào cùng một session (kỳ vọng 18 message, tính cả câu `init`):

   ```text
   run 1: messages=18, câu parallel còn lại=8/8
   run 2: messages=17, câu parallel còn lại=8/8     ← mất 1 câu trả lời
   run 3: messages=15, câu parallel còn lại=6/8     ← mất 2 câu hỏi
   ```

   Cách sửa: dùng **Redis list** với `RPUSH` + `LTRIM` (thao tác atomic ngay trong Redis), như phần "Correct" của đề dùng `r.lrange(f"history:{user_id}", ...)`, hoặc dùng `WATCH`/`MULTI` hay Lua script.
3. Redis chết thì `/chat` trả **500 Internal Server Error** thay vì 503 có thông báo rõ ràng (xem thử nghiệm bên dưới).

### Exercise 5.4: Load balancing

```bash
cd 05-scaling-reliability/production
touch .env.local
docker compose -p k5-day14-scaling up -d --build --scale agent=3
```

```text
NAME                       STATUS                            PORTS
k5-day14-scaling-agent-1   Up 3 seconds (health: starting)   8000/tcp
k5-day14-scaling-agent-2   Up 3 seconds (health: starting)   8000/tcp
k5-day14-scaling-agent-3   Up 3 seconds (health: starting)   8000/tcp
k5-day14-scaling-nginx-1   Up 3 seconds                      0.0.0.0:8080->80/tcp, [::]:8080->80/tcp
k5-day14-scaling-redis-1   Up 14 seconds (healthy)           6379/tcp
```

10 request `POST /chat` qua Nginx (`localhost:8080`); header `X-Served-By` lấy từ `$upstream_addr` trong `nginx.conf`:

```text
[1] served_by=instance-9adb13 storage=redis X-Served-By: 172.20.0.5:8000
[2] served_by=instance-00c645 storage=redis X-Served-By: 172.20.0.3:8000
[3] served_by=instance-c0fe9b storage=redis X-Served-By: 172.20.0.4:8000
[4] served_by=instance-9adb13 storage=redis X-Served-By: 172.20.0.5:8000
[5] served_by=instance-00c645 storage=redis X-Served-By: 172.20.0.3:8000
[6] served_by=instance-c0fe9b storage=redis X-Served-By: 172.20.0.4:8000
[7] served_by=instance-9adb13 storage=redis X-Served-By: 172.20.0.5:8000
[8] served_by=instance-00c645 storage=redis X-Served-By: 172.20.0.3:8000
[9] served_by=instance-c0fe9b storage=redis X-Served-By: 172.20.0.4:8000
[10] served_by=instance-9adb13 storage=redis X-Served-By: 172.20.0.5:8000
```

`docker compose -p k5-day14-scaling logs agent`, đếm số dòng `POST /chat` của từng instance:

```text
      3 agent-1     (instance-c0fe9b, 172.20.0.4)
      4 agent-2     (instance-9adb13, 172.20.0.5)
      3 agent-3     (instance-00c645, 172.20.0.3)
```

**Cách Nginx chia tải:** tên service `agent` được DNS của Docker trả về cả 3 IP. `upstream agent_cluster { server agent:8000; }` đọc đủ 3 IP này khi Nginx khởi động, rồi chia request **round-robin** (vòng lần lượt .5, .3, .4). `keepalive 16` giữ kết nối tới upstream để dùng lại; log cho thấy mỗi instance chỉ nhận request từ một cổng nguồn duy nhất của Nginx. Agent không publish port ra host, chỉ Nginx mở cổng 8080.

**Khi một instance chết:** `docker stop k5-day14-scaling-agent-2` ngay giữa một session:

```text
[turn1] served_by instance-00c645
[turn2] served_by instance-c0fe9b
$ docker stop k5-day14-scaling-agent-2   (instance-9adb13)
[turn3] served_by instance-00c645 turn= 4
[turn4] served_by instance-00c645 turn= 5
[turn5] served_by instance-c0fe9b turn= 6
[turn6] served_by instance-00c645 turn= 7
history count = 12 | users: ['Turn 1', 'Turn 2', 'Turn 3', 'Turn 4', 'Turn 5', 'Turn 6']
```

Traffic tự chuyển sang 2 instance còn lại, không request nào lỗi (nhờ `proxy_next_upstream error timeout http_503`: kết nối tới instance chết bị lỗi thì Nginx thử instance kế tiếp), và **history vẫn đủ 6 lượt**.

Số `turn` lệch lên 1 vì code tính `turn` sau khi đã thêm câu hỏi hiện tại vào history (dòng 140–154).

Sau `docker start`, agent-2 quay lại rotation với ID mới `instance-383e7d`, vì `INSTANCE_ID` sinh ngẫu nhiên mỗi lần khởi động. Nó không nhận traffic ngay mà sau khoảng 10 s, vì Nginx đánh dấu upstream lỗi trong `fail_timeout` (mặc định 10 s).

**Lưu ý:** Nginx bản open source chỉ phân giải `agent` **một lần** lúc khởi động (`resolver` không áp dụng cho `server` trong `upstream` nếu không có tham số `resolve`). Nếu scale lên 5 agent sau khi Nginx đã chạy thì Nginx không thấy 2 instance mới, phải `nginx -s reload`. Nginx open source cũng không tự gọi `/ready` để kiểm tra (không có active health check); nó chỉ phát hiện instance chết khi kết nối thật bị lỗi.

### Exercise 5.5: Test stateless

```text
$ python test_stateless.py
============================================================
Stateless Scaling Demo
============================================================

Session ID: 35b828d8-9697-46bb-8674-fe2cfa49d338

Request 1: [instance-00c645]
  Q: What is Docker?
Request 2: [instance-c0fe9b]
  Q: Why do we need containers?
Request 3: [instance-9adb13]
  Q: What is Kubernetes?
Request 4: [instance-00c645]
  Q: How does load balancing work?
Request 5: [instance-c0fe9b]
  Q: What is Redis used for?

------------------------------------------------------------
Total requests: 5
Instances used: {'instance-c0fe9b', 'instance-00c645', 'instance-9adb13'}
✅ All requests served despite different instances!

--- Conversation History ---
Total messages: 10
  [user]: What is Docker?...
  [assistant]: Container là cách đóng gói app để chạy ở mọi nơi. Build once...
  [user]: Why do we need containers?...
  ...
  [user]: What is Redis used for?...
  [assistant]: Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã đư...

✅ Session history preserved across all instances via Redis!
```

**Giải thích kết quả:**
- 5 câu của **cùng một session** lần lượt rơi vào **cả 3 instance** (00c645, c0fe9b, 9adb13, 00c645, c0fe9b, đúng thứ tự round-robin).
- Dù vậy, history khi đọc lại có đủ **10 message** (5 câu hỏi, 5 câu trả lời) theo đúng thứ tự. Mỗi instance trước khi trả lời đều đọc history từ Redis rồi ghi lại vào Redis, nên không instance nào cần "nhớ" gì trong RAM.
- Nếu lưu history trong RAM, mỗi instance chỉ thấy phần history của các request mà chính nó đã xử lý (2, 2 và 1 câu). Request đọc history chỉ trả về phần của instance nhận request đó.
- Script **không tự tắt instance nào**, nên mình tự `docker stop` agent-2 giữa cuộc hội thoại (thử nghiệm ở Exercise 5.4): cuộc hội thoại vẫn tiếp tục và không mất lượt nào.

**Thử thêm: Redis chết thì sao** (`docker stop k5-day14-scaling-redis-1`):

```text
{"status":"degraded","instance_id":"instance-383e7d","uptime_seconds":21.2,"storage":"redis","redis_connected":false}  -> HTTP 200   (/health)
{"detail":"Redis not available"}  -> HTTP 503                                                                              (/ready)
Internal Server Error  -> HTTP 500                                                                                         (/chat)
$ docker start k5-day14-scaling-redis-1
{"ready":true,"instance":"instance-383e7d"}  -> HTTP 200
chat -> HTTP 200
session keys sau restart Redis: 25
```

Đúng như thiết kế:
- `/health` vẫn trả **200** (chỉ báo `degraded`), nên platform **không** restart agent.
- `/ready` trả **503**, nên load balancer biết không gửi traffic vào.
- Redis lên lại thì redis-py tự kết nối lại, không phải restart agent, và session vẫn còn (Redis ghi snapshot ra `/data` khi tắt).

Còn thiếu: Redis là **điểm lỗi duy nhất** (single point of failure) của cả hệ thống, nên trên production cần Redis có replica hoặc dùng dịch vụ managed, và `/chat` nên trả 503 thay vì 500.

Dọn stack: `docker compose -p k5-day14-scaling down`.

## Part 6: Final Project — output `check_production_ready.py`

Chạy trong `06-lab-complete/`, bản đầy đủ có Docker (venv Python 3.11, `pip install -r requirements.txt`):

```text
$ python check_production_ready.py
...
✅ 1. Dockerfile (multi-stage, < 500 MB)
     ✅ Docker: build image thành công
     ✅ Docker: image < 500 MB — 273 MB
     ✅ Docker: container chạy được, GET /health → 200
...
✅ 7. Rate limiting
     ✅ Khi chạy: vượt giới hạn → 429 — 429 sau 5 request thành công (RATE_LIMIT_PER_MINUTE=5)
     ✅ Khi chạy: state nằm trong Redis — restart agent vẫn bị 429
✅ 8. Cost guard
     ✅ Khi chạy: hết budget → 402 — 402 ở request thứ 1
...
✅ 10. Structured logging
     ✅ Khi chạy: mọi dòng log của app là JSON hợp lệ — 16 dòng JSON
✅ 11. Graceful shutdown
     ✅ Khi chạy: SIGTERM lúc đang có request → request vẫn 200, process tự thoát — exit code 0
     ✅ Docker: `docker stop` tắt êm (exit code 0, < 10s) — exit code 0, 0.8s
...
================================================================
  📋 Checklist Deliverable
     ✅ Dockerfile (multi-stage, < 500 MB)
     ✅ docker-compose.yml (agent + redis)
     ✅ .dockerignore
     ✅ Health check endpoint (GET /health)
     ✅ Readiness endpoint (GET /ready)
     ✅ API Key authentication
     ✅ Rate limiting
     ✅ Cost guard
     ✅ Config từ environment variables
     ✅ Structured logging
     ✅ Graceful shutdown
     ✅ Public URL ready (Railway / Render config)
----------------------------------------------------------------
  Result: 12/12 tiêu chí đạt · 43/43 checks passed
  🎉 PASS 12/12 — PRODUCTION READY! Deploy nào!
================================================================
```

### Các quyết định thiết kế

| Thành phần | Cách làm | Vì sao |
|---|---|---|
| `config.py` | Dataclass đọc toàn bộ cấu hình từ env, có kiểm tra kiểu (`RATE_LIMIT_PER_MINUTE=abc` báo lỗi rõ ràng). `AGENT_API_KEY` **không có giá trị mặc định**: thiếu key ở `staging`/`production` thì dừng ngay lúc khởi động | Không có secret nằm trong code. Cấu hình sai thì lộ ra ngay lúc deploy, không phải chờ tới request đầu tiên |
| `auth.py` | `APIKeyHeader("X-API-Key")`, so sánh bằng `hmac.compare_digest`. Thiếu hoặc sai key **đều trả 401** | Chống tấn công timing (điểm yếu thấy ở Part 4). Chưa cấu hình key thì từ chối mọi request chứ không mở cửa |
| `rate_limiter.py` | Sliding window bằng Redis sorted set. Toàn bộ "dọn cũ → đếm → thêm" chạy trong **một Lua script atomic**. Key là `ratelimit:<sha256(api_key)[:16]>` | Có 2 worker và nhiều instance cùng dùng chung bộ đếm. Lua tránh race condition (ở Part 3 thấy lời giải không atomic lọt tới 4.6 lần giới hạn). Không lưu API key dạng rõ trong Redis |
| `cost_guard.py` | Chi phí toàn hệ thống theo ngày UTC (`cost:YYYY-MM-DD`, TTL 2 ngày). **Giữ chỗ trước, trừ sau**: trước khi gọi LLM, Lua kiểm tra `đã tiêu + ước tính ≤ budget` rồi cộng phần ước tính vào luôn; sau khi có kết quả thì cộng phần chênh lệch; LLM lỗi thì trả lại phần đã giữ | Request cuối không đẩy tổng chi phí vượt budget được, request song song không cùng lọt qua (sửa 2 lỗi của bản Part 4) |
| `redis_client.py` | `redis.from_url(...)` với timeout 2 s, không ping lúc import | Container khởi động được khi chưa có Redis. `/ready` không bị treo khi Redis chết |
| `/health` | Luôn 200, **không** kiểm tra Redis | Liveness chỉ hỏi "process còn sống không". Redis chết mà restart agent thì cũng vô ích |
| `/ready` | 503 khi Redis không ping được **hoặc** khi đang tắt | Load balancer chỉ gửi traffic vào instance phục vụ được |
| Xử lý lỗi | Exception handler cho `HTTPException`, lỗi validate (422 với danh sách field lỗi gọn), `RedisError` (503 + `Retry-After`, **fail closed**: không kiểm tra được giới hạn thì không gọi LLM), và `Exception` (500 với `"Internal server error"`; traceback chỉ ghi vào log) | Mã lỗi rõ ràng, không lộ stack trace ra client |
| Logging | `JsonFormatter` tự viết (`json.dumps`), logger `agent` có `propagate=False`. Middleware ghi một dòng JSON cho mỗi request (`request_id`, `path`, `status`, `ms`); bỏ qua `/health` cho đỡ nhiễu. **Không log API key hay nội dung câu hỏi**, chỉ log độ dài | Tránh lỗi JSON lồng trong chuỗi đã thấy ở Part 1. Traceback cũng nằm gọn trong 1 dòng JSON (field `exc`) |
| Graceful shutdown | Trong `lifespan` startup, cài handler SIGTERM đặt `shutting_down = True` (để `/ready` trả 503) rồi **gọi lại handler của uvicorn**. Uvicorn ngừng nhận kết nối mới, đợi request đang chạy xong (`--timeout-graceful-shutdown 8`), rồi chạy phần shutdown của `lifespan` (đóng Redis) | Phần nhận xét ở Part 5: chuyển readiness ngay khi nhận SIGTERM. Không gọi lại handler cũ thì process không bao giờ tắt |
| `/ask` | Khai báo `def` thường, không `async` | FastAPI chạy nó trong threadpool, nên LLM blocking không chặn event loop (lỗi `/health` bị trễ 2.5 s đã thấy ở Part 5) |
| Dockerfile | Builder cài vào `/opt/venv`, runtime `python:3.11-slim` chỉ copy venv + `app/` + `utils/`, user `app`, có `HEALTHCHECK`. `CMD ["sh","-c","exec uvicorn ... --port ${PORT:-8000} --workers ${WEB_CONCURRENCY:-2}"]` | `exec` thay shell bằng uvicorn, nên uvicorn là PID 1 và nhận SIGTERM trực tiếp, mà vẫn đọc được `$PORT` do platform cấp |
| Compose | `agent` (build) + `redis:7-alpine` (`appendonly yes`, volume, healthcheck, không publish port). Agent có `REDIS_URL=redis://redis:6379/0` và `depends_on: condition: service_healthy`; `.env` có `required: false` | Một lệnh là chạy cả stack. Restart Redis cũng không mất bộ đếm |

### Kiểm tra thêm ngoài script chấm (trên stack `docker compose`, `ENVIRONMENT=staging`)

```text
/health 200 · /ready 200 · /ask không key 401 · sai key 401 · đúng key 200
{"question":"What is deployment?","answer":"Deployment là quá trình ...","usage":{"input_tokens":4,"output_tokens":21,"cost_usd":1.32e-05,"requests_remaining":9}}

Input sai (đúng key) — không có stack trace:
question rỗng → 422 {"detail":"Invalid request body","errors":[{"field":"question","msg":"String should have at least 1 character"}]}
thiếu question → 422 {"detail":"Invalid request body","errors":[{"field":"question","msg":"Field required"}]}
JSON hỏng     → 422 {"detail":"Invalid request body","errors":[{"field":"body","msg":"JSON decode error"}]}
quá 2000 ký tự → 422 · GET /ask → 405
```

**30 request song song** (giới hạn 10/phút, 2 worker): **đúng 10 × 200, 20 × 429**, Lua script giữ đúng giới hạn khi chạy nhiều worker.

```text
HTTP/1.1 429 Too Many Requests
retry-after: 60
x-ratelimit-limit: 10
x-ratelimit-remaining: 0
x-request-id: 39bd3844d3ea

{"detail":{"error":"Rate limit exceeded","limit_per_minute":10,"retry_after_seconds":60}}
```

**Restart agent** (rebuild + recreate container): request tiếp theo vẫn bị **429**, vì bộ đếm nằm trong Redis chứ không nằm trong RAM.

**Redis chết khi agent đang chạy** (`docker compose stop redis`):

```text
/health → 200 {"status":"ok",...}                              (không restart agent oan)
/ready  → 503 {"ready":false,"reason":"redis_unavailable"}     (rút khỏi load balancer)
/ask    → 503 {"detail":"Service temporarily unavailable"} + Retry-After: 5   (fail closed)
Redis lên lại → /ready 200, /ask 200 (tự kết nối lại, không cần restart agent)
```

**Log**: 11/11 dòng log app là JSON hợp lệ, API key xuất hiện **0 lần** trong log. Ví dụ:

```json
{"ts": "2026-11-26T09:24:23.034+00:00", "lvl": "ERROR", "logger": "agent", "event": "redis_unavailable", "path": "/ask", "error": "ConnectionError"}
{"ts": "2026-11-26T09:24:26.652+00:00", "lvl": "INFO", "logger": "agent", "event": "llm_call", "user_id": null, "question_chars": 2, "input_tokens": 2, "output_tokens": 20, "cost_usd": 1.23e-05}
{"ts": "2026-11-26T09:24:26.653+00:00", "lvl": "INFO", "logger": "agent", "event": "request", "request_id": "748117c38197", "method": "POST", "path": "/ask", "status": 200, "ms": 113.4}
```

**Container:** chạy bằng `user=app`; PID 1 là `/opt/venv/bin/python /opt/venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2 ...`; trạng thái `healthy`.

**`docker stop` khi đang có request:**

```text
[0.109231s] request in-flight -> HTTP 200
docker stop mất 0.66s, exit code=0
{"event": "sigterm_received", "signal": 15}     ← cả 2 worker đều log
INFO:     Shutting down
{"event": "shutdown_complete"}
INFO:     Stopping parent process [1]
```

## AI log

| Checkpoint | Công cụ | Dùng để làm gì | Mình tự kiểm tra lại thế nào |
|---|---|---|---|
| CP0–CP1 | Claude Code | Đọc đề, chạy bản develop và production, gom output curl và log, soạn bảng anti-pattern và bảng so sánh | Tự chạy lại các lệnh curl, đối chiếu số dòng trong bảng với code, kiểm tra hai lỗi logging và `.env` bằng cách chạy lại có và không có `OPENAI_API_KEY` |
| CP2 | Claude Code | Build và chạy image, đo dung lượng, chạy stack Compose, thử rate limit của Nginx và layer cache, soạn câu trả lời và sơ đồ | Số MB lấy từ `docker images` và `docker image inspect` chạy trên máy mình, không chép từ README. Tự chạy lại lệnh curl qua Nginx và vòng lặp song song để thấy 429 |
| CP3 | Claude Code | Chạy bản develop và production của Part 4, gom output `curl -i` (đã che token), đọc code auth, rate limiter, cost guard. Viết bản cost guard dùng Redis và Lua, test với Redis thật, so sánh race condition với lời giải mẫu | Tự chạy lại vòng lặp 20 request để thấy 429. Đọc lại Lua script và hiểu vì sao nó atomic. Kiểm tra token đã được che trước khi dán vào bài |
| CP4 | Claude Code | Chạy health/ready và thử SIGTERM so với SIGKILL ở bản develop. Chạy stack 3 agent, quan sát round-robin, chạy `test_stateless.py`, tự tắt 1 instance, tắt Redis, thử request song song cùng session | Tự chạy lại `test_stateless.py` và vòng 10 request. Đối chiếu `served_by` với `X-Served-By` và log. Tự kiểm tra lost update bằng cách đếm message trong history |
| CP5 | Claude Code | Đọc hợp đồng trong README và script chấm, viết code cho các TODO trong `06-lab-complete/` (config, auth, rate limiter và cost guard dùng Redis + Lua, log JSON, health/ready, graceful shutdown, Dockerfile, compose, railway/render), chạy script chấm tới khi PASS 12/12, test thêm trên stack compose | Đọc lại từng file và hiểu từng quyết định ở bảng trên. Tự chạy `python check_production_ready.py` bản đầy đủ. Tự thử tắt Redis, request song song và `docker stop` khi đang có request |
| CP6 | Claude Code | Cài Railway CLI, tạo project, Redis, đặt biến môi trường (secret qua stdin), `railway up`, tạo domain, chạy 4 lệnh tự kiểm và `--public-url`, viết `DEPLOYMENT.md`, so sánh `railway.toml`/`render.yaml`, đọc pipeline Cloud Run | Mình tự đăng nhập Railway và chụp ảnh dashboard. Tự chạy lại 4 lệnh tự kiểm trên URL public. Kiểm tra repo không chứa key |
| CP7 | Claude Code | Chạy agent v1/v2, thêm metric `agent_question_length_chars`, chạy load test 4 mức concurrency và 3 mức `MOCK_SLOW_RATE`, eval v1/v2, sửa `KNOWLEDGE_BASE`, viết job CI quality gate, điền mục 1–3 của `BENCHMARK_REPORT.md` từ file JSON | Đối chiếu mọi con số trong báo cáo với file JSON trong `develop/reports/`. Phát hiện và chạy lại lượt `MOCK_SLOW_RATE` đầu tiên (agent chưa thật sự khởi động lại). Tự chạy lại lệnh eval cuối để thấy `✅ PASS` |
| CP8 | Claude Code | Bật stack observability, sửa lỗi `PYTHONUSERBASE` trong `production/Dockerfile`, chạy benchmark scaling 1 và 3 instance (phát hiện `docker compose run` tự đổi số replicas nên chạy lại với `--no-deps`), đo Exercise 7.4 bằng PromQL, tìm trace chậm/lỗi qua Jaeger API, chạy 5 thí nghiệm A–E và ghi thời điểm alert chuyển trạng thái bằng script, chụp 4 ảnh Part 7 bằng Playwright (Chromium headless), điền mục 4–6 của `BENCHMARK_REPORT.md`, đóng gói `reports/` và `benchmark-results/` | Đối chiếu tự động mọi số trong báo cáo với file JSON/CSV. Kiểm tra số container và số target Prometheus trước và sau mỗi lượt benchmark. Xem lại từng ảnh và đối chiếu `trace_id` trong ảnh với dòng log |
