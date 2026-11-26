# Benchmark & Observability Report — Day 14

> Mọi con số lấy từ report JSON / CSV / screenshot thật, không ước lượng.
> JSON của Bước 1 nằm ở `06-lab-complete/benchmark-observability/develop/reports/` (bản nộp copy ra `reports/` ở gốc repo).

**Họ tên:** Đoàn Trí Hùng
**Ngày chạy:** 2026-11-26
**Môi trường:** Laptop Linux, Intel Core i5-10300H (4 nhân / 8 luồng), 14 GB RAM, Python 3.11.18. Agent `develop/app.py` chạy **1 process uvicorn** (1 event loop); `load_test.py` chạy trên **cùng máy**.

---

## 1. SLO của agent

| SLI | SLO | Lý do chọn ngưỡng này |
|---|---|---|
| p95 latency `/ask` | < **500** ms | Ở tải bình thường (≤ 20 request song song) p95 đo được 225–276 ms (mục 2.1, 2.2), nên 500 ms chừa khoảng **2 lần** cho dao động mà vẫn dưới ngưỡng người dùng thấy "chậm" với một câu trả lời ngắn. Đặt theo **p95** chứ không theo average, vì average che mất tail latency (mục 2.2) |
| Error rate `/ask` | < **2** % | Upstream LLM (mock) lỗi ngẫu nhiên khoảng 1% (`MOCK_ERROR_RATE=0.01`); SLO 2% để error budget gấp đôi mức nền. Lưu ý: 300 request là mẫu nhỏ, error rate dao động 0.67–2.33% giữa các lần chạy dù cấu hình không đổi, nên SLO error rate phải đánh giá trên cửa sổ dài (vd 30 ngày), không theo một lần load test |
| Accuracy trên golden dataset | ≥ **85** % | Cùng ngưỡng với job CI (`--min-accuracy 0.85`). v2 ban đầu đạt 91.3%; 85% cho phép sai tối đa 3/23 câu, kèm điều kiện **không có regression** so với baseline. Bản phát hành chính thức dùng gate chặt hơn (`--min-accuracy 1.0`, mục 3) |

---

## 2. Benchmark hiệu năng (develop, `load_test.py`)

### 2.0 Exercise 7.1: Đọc metrics của agent

Sau 1 request `POST /ask`, `curl http://localhost:8002/metrics | grep -v "^#"`:

| Loại | Ví dụ trong output | Ý nghĩa |
|---|---|---|
| **Counter** (chỉ tăng) | `http_requests_total{method="POST",route="/ask",status="200"} 1.0`, `llm_tokens_total{model="mock-llm-small",type="input"} 91.0`, `llm_cost_usd_total{model="mock-llm-small"} 4.665e-05` | Dùng với `rate()` để ra req/s, token/s, USD/s |
| **Gauge** (lên xuống) | `agent_in_flight_requests 0.0`, `agent_info{model="mock-llm-small",version="v2"} 1.0` | Saturation: số request đang xử lý; `agent_info` luôn bằng 1, chỉ để mang label version/model |
| **Histogram** | `http_request_duration_seconds_bucket{le="0.2",method="POST",route="/ask"} 0.0` … `_count 1.0`, `_sum 0.208` | Đếm số request theo từng bucket latency, nên tính được p95 bằng `histogram_quantile(0.95, rate(..._bucket[5m]))` |

**Nếu dùng `request.url.path` làm label `route`:** mỗi URL lạ mà bot gọi (`/wp-admin`, `/.env`, `/a1b2c3`…) sẽ thành **một time series mới**, và nhân thêm với số giá trị của `method` × `status` × số bucket của histogram (12 bucket). Bot quét vài nghìn URL là sinh ra hàng chục nghìn series, khiến Prometheus tốn RAM, query chậm và có thể sập (cardinality explosion). Code dùng `route.path` (template) nên số series bị chặn bởi số route định nghĩa trong app. Mình đã thử gọi 3 URL lạ: cả 3 dồn vào **một** series duy nhất:

```text
/wp-admin -> 404   /.env -> 404   /a1b2c3 -> 404
http_requests_total{method="GET",route="unmatched",status="404"} 3.0
```

**Nhiệm vụ: thêm metric `agent_question_length_chars`** (`develop/app.py`):

```python
QUESTION_LENGTH = Histogram(
    "agent_question_length_chars", "Độ dài câu hỏi gửi tới /ask (ký tự)",
    buckets=(20, 50, 100, 200, 500, 1000, 2000),
)
# đầu endpoint ask():
QUESTION_LENGTH.observe(len(body.question))
```

Kết quả sau 1 câu hỏi 40 ký tự: `agent_question_length_chars_count 1.0`, `agent_question_length_chars_sum 40.0`. Dùng Histogram chứ không dùng label `length="40"`: độ dài có vô số giá trị, làm label thì cardinality bùng nổ đúng như ví dụ trên.

### 2.1 Concurrency vs latency (agent v2)

`python load_test.py --url http://localhost:8002 --requests 300 --concurrency {1,10,50,100} --out reports/load_v2_c{N}.json`, `MOCK_SLOW_RATE=0.03` (mặc định):

| Concurrency | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Error rate | SLO |
|---|---|---|---|---|---|---|
| 1 | 3.99 | 192.2 | 224.9 | 1700.5 | 1.00% (3/300) | ✅ PASS |
| 10 | 35.38 | 198.0 | 236.6 | 1708.7 | 2.00% (6/300) | ✅ PASS |
| 50 | 87.57 | 361.3 | 752.3 | 1728.8 | 2.33% (7/300) | ❌ FAIL (p95, error rate) |
| 100 | 51.27 | 745.4 | 3694.5 | 4220.1 | 1.67% (5/300) | ❌ FAIL (p95) |

(avg lần lượt: 252.3 / 253.7 / 425.9 / 1308.1 ms. Mọi lỗi đều là `503` do mock LLM.)

**Nhận xét:** RPS **không tăng tuyến tính**. Định luật Little cho `RPS ≈ concurrency / latency trung bình`:

| Concurrency | RPS lý tưởng (latency giữ ~250 ms) | RPS đo được | Latency avg đo được |
|---|---|---|---|
| 1 | 4 | 3.99 | 252 ms |
| 10 | 40 | 35.38 | 254 ms |
| 50 | 200 | 87.57 | 426 ms |
| 100 | 400 | 51.27 | 1308 ms |

- **1 → 10:** gần tuyến tính (×8.9). Mock LLM dùng `asyncio.sleep` nên chờ LLM không chiếm CPU, và một event loop phục vụ được nhiều request cùng lúc. Latency gần như không đổi.
- **10 → 50:** RPS chỉ tăng ×2.5, còn **p50 tăng từ 198 lên 361 ms**: request bắt đầu **phải xếp hàng**. Phần việc tốn CPU của mỗi request (validate Pydantic, serialize JSON response, ghi 2 dòng log JSON, cập nhật Prometheus) đều chạy trên **một** event loop của **một** process, nên CPU của process đó trở thành nút thắt. Load generator chạy cùng máy cũng tranh CPU.
- **50 → 100:** RPS **giảm** (87.6 xuống 51.3), p95 lên 3.7 s. Hệ thống đã quá điểm bão hoà: thêm request song song chỉ làm hàng đợi dài thêm, cộng chi phí context-switch và tạo kết nối. Với 300 request mà mỗi lần có 100 request song song, thời gian chạy (5.85 s) bị kéo dài bởi phần đuôi chậm.
- **Kết luận:** một instance chịu được khoảng **20–30 request song song** trong SLO. Muốn tăng throughput thì phải **scale ngang** (nhiều worker hoặc instance sau load balancer, mục 5), không thể chỉ tăng concurrency.

### 2.2 Tail latency

Khởi động lại v2 với từng giá trị `MOCK_SLOW_RATE` (đã kiểm tra PID và `uptime` để chắc process thật sự được khởi động lại), mỗi lần `--requests 300 --concurrency 20`:

| `MOCK_SLOW_RATE` | avg (ms) | p95 (ms) | p99 (ms) |
|---|---|---|---|
| 0 | 206.1 | 260.6 | 302.0 |
| 0.03 (mặc định) | 239.2 | 275.5 | 1714.8 |
| 0.1 | 317.4 | 1692.3 | 1720.0 |

(p50 gần như không đổi: 203.6 / 201.9 / 200.3 ms. RPS giảm 94.6 → 61.0 → 50.2 vì mỗi worker của load test phải chờ request chậm xong mới gửi request tiếp.)

**Nhận xét:**
- **Chỉ số nhạy nhất là percentile nằm trong vùng request chậm.** Từ 0 lên 3%: **p99 tăng ×5.7** (302 → 1715 ms) vì 3% > 1%, nên p99 rơi vào nhóm request chậm (+1500 ms). Trong khi đó avg chỉ +16% (206 → 239) và p95 gần như đứng yên (+15 ms) vì 3% < 5%. Từ 3% lên 10%: **p95 tăng ×6.1** (276 → 1692 ms) vì giờ 10% > 5%, còn avg chỉ +33%.
- **Vì sao avg chỉ tăng nhẹ:** phần chậm thêm `tỉ lệ chậm × 1500 ms` bị chia đều cho mọi request. Lý thuyết khoảng +150 ms ở mức 10%; đo được +111 ms (206 → 317), cùng bậc độ lớn, chênh do mẫu chỉ 300 request. Vì vậy avg trông vẫn "ổn". Nhưng với 10% người dùng thì họ thực sự chờ khoảng 1.7 s.
- **Vì sao SLO không nên đặt theo average:** với `MOCK_SLOW_RATE=0.1`, avg = 317 ms vẫn **đạt** ngưỡng 500 ms, trong khi 1/10 request mất tới 1.7 s. SLO theo average sẽ báo "khoẻ" ngay lúc người dùng đang chịu chậm. Đặt SLO theo **p95/p99** thì thấy được trải nghiệm của nhóm người dùng tệ nhất. Thực tế lần này SLO p95 đã **FAIL** đúng ở mức 0.1.

---

## 3. Benchmark chất lượng (develop, `eval/run_eval.py`)

| Chỉ số | Agent v1 | Agent v2 | Agent v2 (sau khi sửa KB) |
|---|---|---|---|
| Accuracy | 4.3% (1/23) | 91.3% (21/23) | **100.0% (23/23)** |
| Avg tokens / câu | 57.7 | 108.8 | 116.4 |
| Tổng cost (USD) | 0.000413 | 0.000757 | 0.000814 |
| Cost / câu đúng (USD) | 0.00041295 | 0.00003607 | 0.00003540 |
| Latency p50 / p95 (ms) | 160.1 / 193.2 | 186.5 / 219.3 | 183.5 / 211.1 |
| Quality gate | ❌ FAIL (≥ 80%) | ✅ PASS (≥ 80%, 0 regression so với v1) | ✅ PASS (**≥ 100%**, 0 regression so với v2) |

Lệnh: `eval_v1.json` (v1, không baseline) → `eval_v2.json` (`--baseline reports/eval_v1.json`) → sửa KB, khởi động lại v2 → `eval_v2_fixed.json` (`--min-accuracy 1.0 --baseline reports/eval_v2.json`, in ra `Quality gate (accuracy ≥ 100%, không regression): ✅ PASS`).

**Những câu v2 trả lời sai ban đầu và cách bạn sửa:**

| ID | Keyword bị thiếu | Bạn đã sửa gì |
|---|---|---|
| `scaling-03` ("Kubernetes HPA scale dựa trên những chỉ số nào?") | `cpu`, `memory`, `custom metrics` | Retrieval không khớp tài liệu nào nên v2 trả lời chung chung. Thêm entry vào `KNOWLEDGE_BASE` với từ khoá `["hpa", "autoscal", "kubernetes"]`, nội dung nói HPA scale theo CPU, memory hoặc custom metrics (RPS, độ dài hàng đợi) |
| `security-02` ("JWT khác gì API key trong xác thực?") | `token`, `hết hạn`, `chữ ký` | Thêm entry `["jwt", "api key"]`: API key là chuỗi cố định, không hết hạn; JWT là token có chữ ký số, chứa thời điểm hết hạn, server chỉ cần xác minh chữ ký |

Trước khi thêm, mình kiểm tra không câu nào khác trong dataset chứa các từ khoá mới (tránh "cướp" retrieval của câu đang đúng). Kết quả: **+2 câu cải thiện, 0 regression**.

**Nhận xét về trade-off chất lượng / chi phí / latency giữa v1 và v2:**
- v2 (RAG) đưa tài liệu vào prompt nên **token/câu tăng 89%** (57.7 → 108.8), **tổng cost tăng 83%**, và **p95 tăng 26 ms** (thêm bước retrieval khoảng 20 ms, cộng prompt dài hơn làm LLM chậm hơn chút).
- Đổi lại, accuracy tăng từ 4.3% lên 91.3%. Tính theo **cost / câu đúng**, v2 **rẻ hơn khoảng 11.4 lần** ($0.000413 so với $0.0000361): v1 tốn ít token mỗi câu nhưng gần như toàn trả lời sai, tức là tốn tiền vô ích. Vì vậy "cost / câu đúng" mới là chỉ số đúng để so hai phiên bản, còn "cost / câu" sẽ đánh giá nhầm v1 là tốt hơn.
- Bản sửa KB tăng thêm 7% token (tài liệu mới dài hơn) nhưng cost / câu đúng vẫn **giảm** (0.0000361 → 0.0000354) vì có thêm 2 câu đúng.

### 3.1 Quality gate trong CI

**Exit code:** chạy eval **v1** với `--baseline reports/eval_v2.json` thì gate báo `❌ FAIL` (accuracy 4.3% < 80%, **20 câu regression**), và `run_eval.py` trả **exit code 1**. Bản đạt gate trả **exit code 0**.

**Job CI:** [`.github/workflows/quality-gate.yml`](.github/workflows/quality-gate.yml) chạy trên mỗi pull request đụng tới `develop/`:
1. Cài Python 3.11 và `requirements.txt`.
2. Bật agent v2 nền (`MOCK_ERROR_RATE=0`, để gate không fail vì lỗi ngẫu nhiên không liên quan tới chất lượng), chờ `/health`.
3. Chạy `python eval/run_eval.py --url http://localhost:8000 --min-accuracy 0.85`. **Exit code 1 làm step fail, job fail**, và nếu bật branch protection "Require status checks to pass" cho job `eval` thì **PR không merge được**.
4. Upload `eval_ci.json` và log agent làm artifact để xem câu nào sai.

Mình đã chạy thử đúng các lệnh của job ở máy local: v2 sau khi sửa → `✅ PASS`, exit 0; giả lập một PR đổi agent về v1 → `❌ FAIL`, exit 1.

---

## 4. Observability stack (production)

Stack `06-lab-complete/benchmark-observability/production/`, bật bằng `docker compose -p k5-day14-observability up -d --build` (agent + nginx + prometheus + grafana + jaeger + locust). Tải nền cho mục 4 là Locust headless **30 users, ramp 5** (`-u 30 -r 5`), 2 instance agent.

> **2 lỗi của bản khung, đã sửa trước khi đo:**
> 1. **Agent không khởi động được** (`ModuleNotFoundError: No module named 'uvicorn'`, container restart liên tục). User `agent` được tạo với home là `/app` (`useradd -d /app`) nên Python tìm package ở `/app/.local`, trong khi package nằm ở `/home/agent/.local`. Sửa bằng `ENV PYTHONUSERBASE=/home/agent/.local` trong `production/Dockerfile` (cách mà Dockerfile ở Part 5 đã làm).
> 2. **Lệnh benchmark scaling trong đề làm sai số instance.** `docker compose run locust ...` tự khởi động lại các service phụ thuộc theo `deploy.replicas: 2`, nên lượt "1 instance" bị tăng lên 2 và lượt "3 instance" bị giảm xuống 2. Mình phát hiện qua Prometheus (`req/s theo instance` có 2 IP ở cả 2 lượt) và `docker events` (`agent-2` được tạo đúng giây Locust bắt đầu). Đã xoá CSV sai, chạy lại với `docker compose run --no-deps ...` và kiểm tra số container và số target Prometheus **trước và sau** mỗi lượt.
>
> Ngoài ra `production/utils/mock_llm.py` được chép phần sửa `KNOWLEDGE_BASE` từ Bước 1 (README, Lưu ý số 10), nên eval qua stack cũng đạt 23/23.

### 4.0 Exercise 7.4: Đọc dashboard khi có tải (30 users)

Số đo bằng PromQL trên cửa sổ 2 phút, lúc 17:34:20, khi tải đã ổn định:

1. **p95 và avg:** p95 = **296.6 ms** (panel Latency p95 hiển thị 295.77 ms), avg = **243.0 ms**, p50 = 187.7 ms, nhưng **p99 = 1844.8 ms**. p95 chỉ cách avg khoảng 54 ms, tức là 95% request khá đồng đều (LLM khoảng 150–200 ms cộng retrieval). Nhưng p99 gấp **7.6 lần** avg: khoảng 3% request bị LLM chậm thêm khoảng 1.5 s (`MOCK_SLOW_RATE=0.03`). Vì 3% < 5% nên p95 không thấy phần đuôi này, p99 mới thấy. Khoảng cách avg đến p99 chính là tail latency, và average che mất nó.
2. **Nginx có chia đều không:** có. Panel "Requests/s theo instance" cho thấy 2 instance nhận **7.82 và 7.57 req/s** (50.8% / 49.2%). Khi scale lên 3 (mục 5): 19.71 / 19.32 / 19.54 req/s. Nginx round-robin, và nhờ `server agent:8000 resolve` nó tự thấy instance mới sau khoảng 10 s mà không phải restart.
3. **Vì sao input tokens/s lớn hơn output tokens/s:** **1211.7 input/s so với 646.5 output/s**, tức trung bình **79.4 token vào, 42.4 token ra** mỗi request. Prompt gồm *system prompt + tài liệu retrieve được (RAG của v2) + câu hỏi*, còn câu trả lời chỉ là một đoạn ngắn. Ở thí nghiệm E (v1, không có context) input giảm còn 671/s: phần chênh lệch chính là chi phí của context RAG. Với giá model (output đắt gấp 4 lần input), cả hai đều quan trọng khi tính cost.
4. **`curl http://localhost:8080/metrics` trả về gì:** **`HTTP/1.1 404 Not Found`**. Nginx chặn bằng `location = /metrics { return 404; }` vì metrics lộ thông tin nội bộ (route, lưu lượng, version, chi phí). Prometheus vẫn đọc được vì nó **scrape trực tiếp `agent:8000/metrics` trong mạng nội bộ `obs_net`** (DNS service discovery), không đi qua Nginx. Mình kiểm tra từ trong container nginx: `wget http://agent:8000/metrics` trả metric bình thường.

Unit test alert rules: `promtool test rules /p/alert_rules_test.yml` → `SUCCESS`.

### 4.1 Screenshot

- [x] Dashboard Grafana khi đang có tải: [`screenshots/grafana-dashboard.png`](screenshots/grafana-dashboard.png) (20 phút gồm 2 lượt benchmark scaling và tải 30 users; Traffic 18.5 req/s, Error rate 0.98%, p95 295.77 ms, 2 instances up)
- [x] Một trace chậm trong Jaeger: [`screenshots/jaeger-slow-trace.png`](screenshots/jaeger-slow-trace.png) (3.6 s, span `chat mock-llm-small` 1.8 s, mục 4.3)
- [x] Một trace lỗi trong Jaeger: [`screenshots/jaeger-error-trace.png`](screenshots/jaeger-error-trace.png) (`POST /ask` 503; span `chat mock-llm-small` có `error.type = upstream_unavailable` và event **`exception`**: `exception.message = Upstream LLM error: 503 Service Unavailable (mock)`)
- [x] Trang Alerts của Prometheus với **2 alert cùng firing**: [`screenshots/prometheus-alert-firing.png`](screenshots/prometheus-alert-firing.png) (`AgentHighErrorRate` value 0.1875 + `AgentInstanceDown` instance `172.20.0.9:8000`, chụp lúc 17:42 khi thí nghiệm A và D chạy chồng nhau)

**Trace lỗi ↔ log:** dòng log `llm_error` cuối cùng:

```json
{"ts": "2026-11-26T10:35:31.316620+00:00", "level": "INFO", "service": "observable-agent", "instance": "3bd241a592e2", "event": "llm_error", "trace_id": "9aac0953c79276acb617cc9b3cd0753c", "span_id": "3418a20fd46f96ad", "request_id": "c3b065315bed", "error": "Upstream LLM error: 503 Service Unavailable (mock)"}
```

Dán `trace_id` `9aac0953c79276acb617cc9b3cd0753c` vào Jaeger ra đúng trace lỗi trong ảnh: `POST /ask` 206 ms, status ERROR; `agent.run` 193 ms (ERROR + event exception); `agent.retrieve_context` 77 ms; `chat mock-llm-small` 113 ms (ERROR, `error.type=upstream_unavailable`, event `exception` với `exception.type = utils.mock_llm.LLMError`).

### 4.2 Thí nghiệm "phá hệ thống"

Locust chạy 30 users suốt bài; đổi cấu hình bằng `production/.env` rồi `docker compose -p k5-day14-observability up -d --scale agent=2 agent`. Thời điểm chuyển trạng thái alert lấy từ script đọc `GET /api/v1/alerts` mỗi 2 giây.

| # | Thay đổi | Alert nào firing | Thời gian từ lúc đổi → firing | Panel nào phản ánh rõ nhất |
|---|---|---|---|---|
| A | `MOCK_ERROR_RATE=0.2` | `AgentHighErrorRate` | **1 phút 36 s** (đổi 17:38:06 → pending 17:38:42 → firing 17:39:42) | **Error rate** (0.98% → ~20%; giá trị alert 0.1875), Requests/s theo status code (503 tăng), LLM calls `error` |
| B | `MOCK_SLOW_RATE=0.3` | `AgentHighLatencyP95` | **2 phút 32 s** (17:43:31 → pending 17:44:03 → firing 17:46:03) | **Latency /ask p50/p95/p99**: p95 = 1924 ms, p99 = 1985 ms trong khi avg = 690 ms và p50 = 241 ms gần như không đổi |
| C | `LLM_MODEL=mock-llm-large` | `LLMCostBurnRateHigh` | **2 phút 29 s** (17:46:24 → pending 17:46:53 → firing 17:48:53) | **Cost burn rate**: $1.63/giờ → **$35.62/giờ** (rate 2 phút); LLM latency p95 0.198 s → 0.497 s (×2.5) |
| D | `docker pause k5-day14-observability-agent-1` | `AgentInstanceDown` (`172.20.0.9:8000`) | **44 s** (17:39:54 → pending 17:40:08 → firing 17:40:38) | **Instances up** 2 → 1. Locust ghi **36 lỗi 504** (17:40:03–17:42:29, `benchmark-results/bg_30users_failures.csv`) nhưng panel Error rate của agent **không** tăng vì lỗi này nằm ở Nginx |
| E | `AGENT_VERSION=v1` | **Không có** (theo dõi 17:50:02–17:54:56; `LLMCostBurnRateHigh` còn sót từ C hết firing lúc 17:54:54 khi cửa sổ 5 phút trôi qua) | – | **Retrieval hit ratio** 100% → không còn dữ liệu; **Tokens/s** input 1247 → 671, output 663 → 365; bảng **Phiên bản đang chạy** đổi sang `v1`. Error rate 1.17%, p95 280 ms vẫn xanh |

**Phân tích thời gian tới lúc firing:** `thời gian ≈ (thời gian để rate trên cửa sổ vượt ngưỡng) + for + chu kỳ scrape/evaluate (5 s)`.
- **A:** sau khoảng 36 s, `rate(...[1m])` đã có đủ request lỗi mới để vượt 5% (pending), rồi chờ thêm `for: 1m`.
- **B:** p95 vượt 1 s sau khoảng 32 s, cộng `for: 2m`.
- **C:** chỉ cần khoảng 29 s là `rate(...[5m])` vượt $2/giờ, vì giá model large gấp 16.7 lần (đo được $35.62/giờ so với mức nền $1.63–2.40/giờ), cộng `for: 2m`.
- **D:** nhanh nhất: `up == 0` ngay ở lần scrape lỗi đầu tiên (khoảng 14 s, gồm timeout scrape), cộng `for: 30s`.

**Quan sát thêm:**
- **Ngưỡng cost alert đặt quá thấp:** ở tải nền 30 users với v2, chi phí đã là **$2.40/giờ**, nên `LLMCostBurnRateHigh` **firing ngay khi hệ thống bình thường** (từ 17:23 trong lượt benchmark 100 users và cả khi tải 30 users ổn định). Alert mà lúc nào cũng firing sẽ bị bỏ qua (alert fatigue). Nên đặt ngưỡng theo chi phí nền thực tế, ví dụ khoảng 3 lần mức bình thường, hoặc theo % budget ngày.
- **`AgentInstanceDown` vào `pending` mỗi lần recreate container** (Prometheus vẫn scrape IP cũ cho tới khi DNS làm mới sau 10 s) nhưng không firing nhờ `for: 30s`. Đây là ví dụ thực tế cho vai trò của `for`.
- **Đổi cấu hình bằng `up -d` recreate cả 2 agent cùng lúc**, nên Locust ghi hàng trăm lỗi **502** (Nginx gửi request tới IP cũ). Production cần rolling update (thay từng instance một, chờ readiness), không recreate tất cả cùng lúc.
- **A + D chạy chồng nhau** cho thấy alert độc lập với nhau: `AgentHighErrorRate` và `AgentInstanceDown` cùng firing (ảnh Prometheus). Khi bỏ A, error alert tự về `inactive` sau khoảng 47 s.

**Thí nghiệm E không làm alert nào firing. Bạn sẽ phát hiện loại sự cố này trong production bằng cách nào?**

Metric hạ tầng (latency, error, saturation) chỉ trả lời câu hỏi "agent có **chạy** không", chứ không biết agent trả lời **đúng** hay không. Mình chạy eval golden dataset **thẳng vào stack qua Nginx** trong lúc làm E: **v1 đạt 4.3% (1/23), gate ❌ FAIL, exit 1**; trả về v2 thì **100% (23/23), ✅ PASS**. Trong suốt lúc đó dashboard vẫn toàn xanh. Cách phát hiện:
1. **Chặn trước khi deploy:** chạy eval làm quality gate trong CI (job ở mục 3.1). Bản v1 sẽ không bao giờ merge được.
2. **Theo dõi metric chất lượng gián tiếp và đặt alert:** ví dụ `retrieval hit ratio < 0.8` hoặc `absent(rate(agent_retrieval_total{result="hit"}[5m]))` (ở E metric này biến mất hoàn toàn), token/câu hoặc độ dài câu trả lời giảm đột ngột, `agent_info` đổi version ngoài lịch deploy.
3. **Eval sau khi deploy (online):** canary hoặc shadow traffic, chấm tự động một phần traffic thật bằng LLM-as-judge, tỉ lệ người dùng bấm 👎 hoặc hỏi lại, rồi alert khi điểm chất lượng giảm.

### 4.3 Debug bằng trace

Trace chậm nhất trong 30 phút (Jaeger API `/api/v3/traces`, `duration_min=1500ms`, sắp theo duration):

- `trace_id`: `80b58918b3cd49e7eac11b766da957c4`
- Tổng thời gian: **3593.8 ms** (`POST /ask`, bắt đầu 17:29:12.309, instance `3bd241a592e2`)
- Span chiếm nhiều thời gian nhất: **`chat mock-llm-small`** (**1795.2 ms, 50.0%**; `gen_ai.usage.input_tokens=95`, `output_tokens=55`). Các span khác: `agent.run` 1903.5 ms (53.0%), `agent.retrieve_context` 107.7 ms (3.0%)
- Dòng log tương ứng (grep theo `trace_id`):

```json
{"ts": "2026-11-26T10:29:15.606338+00:00", "level": "INFO", "service": "observable-agent", "instance": "3bd241a592e2", "event": "llm_call", "trace_id": "80b58918b3cd49e7eac11b766da957c4", "span_id": "fef18cbe942a5f99", "request_id": "8665c59a64e4", "model": "mock-llm-small", "agent_version": "v2", "input_tokens": 95, "output_tokens": 55, "cost_usd": 4.7e-05, "llm_ms": 1663.1}
{"ts": "2026-11-26T10:29:15.801807+00:00", "level": "INFO", "service": "observable-agent", "instance": "3bd241a592e2", "event": "http_request", "trace_id": "80b58918b3cd49e7eac11b766da957c4", "span_id": "fef18cbe942a5f99", "request_id": "8665c59a64e4", "method": "POST", "route": "/ask", "status": 200, "ms": 3491.4}
```

**Đọc trace:** trace này có **2 nguyên nhân chậm chồng lên nhau**:
1. **LLM chậm** (span `chat` 1.8 s, log `llm_ms: 1663.1`): đây là request rơi vào nhóm `MOCK_SLOW_RATE`, chậm ở phía upstream LLM chứ không phải code của mình.
2. **Khoảng 1690 ms (47%) nằm ngoài `agent.run`**, tức là `POST /ask` bắt đầu từ lâu trước khi agent kịp xử lý. Trace này xảy ra lúc **17:29:12**, giữa lượt benchmark **1 instance + 100 users**: lúc đó instance dùng 0.415/0.5 CPU và có tới 58 request đang xử lý, nên request phải **xếp hàng chờ event loop/CPU**. Bằng chứng phụ: `retrieve_context` mất 108 ms trong khi bình thường khoảng 20 ms, và span `chat` (1795 ms) dài hơn latency mock định sẵn (1663 ms) vì event loop quá tải đánh thức `asyncio.sleep` muộn.

Như vậy chỉ một trace đã phân biệt được "LLM chậm" (cần timeout, retry, model nhanh hơn) với "server quá tải" (cần scale ngang). Đúng như mục 5 cho thấy: lên 3 instance thì p95 giảm từ 1900 xuống 510 ms.

---

## 5. Benchmark scaling (production, Locust headless, 100 users, 2 phút)

```bash
docker compose -p k5-day14-observability up -d --scale agent=1     # (đã kiểm tra: 1 container, 1 target)
docker compose -p k5-day14-observability run --no-deps --rm locust -f /mnt/locust/locustfile.py --host http://nginx \
  --headless -u 100 -r 10 -t 2m --csv /mnt/results/scale_1
docker compose -p k5-day14-observability up -d --scale agent=3     # (đã kiểm tra: 3 container, 3 target)
docker compose -p k5-day14-observability run --no-deps --rm locust ... --csv /mnt/results/scale_3
```

Số liệu dòng `POST /ask` trong `benchmark-results/scale_{1,3}_stats.csv`:

| Số instance | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Failures |
|---|---|---|---|---|---|
| 1 | 47.85 | 420 | 1900 | 3100 | 46 / 5715 (0.80%) |
| 3 | 58.60 | 210 | 510 | 1700 | 66 / 6994 (0.94%) |

(Dòng Aggregated, gồm cả `/health`: RPS 52.83 → 64.30; avg `/ask` 612.1 → 274.6 ms; max 4298 → 2472 ms. Mọi failure đều là `503` do mock LLM lỗi khoảng 1%.)

Prometheus trong đúng 2 phút của mỗi lượt:

| | 1 instance | 3 instance |
|---|---|---|
| HTTP p50 / p95 `/ask` (agent tự đo) | 365 / 1496 ms | 199 / 472 ms |
| **LLM p50 / p95** | **151 / 198 ms** | **152 / 198 ms** |
| CPU mỗi instance (limit 0.5 core) | **0.415** | 0.19 |
| In-flight tối đa | 58 | 24 |
| Req/s mỗi instance | 47.9 | 19.7 / 19.3 / 19.5 |

**Giải thích kết quả:**
- **Scale có cải thiện p95 không:** **có, rõ rệt.** p95 giảm từ 1900 xuống 510 ms (−73%), p50 giảm một nửa (420 → 210 ms), p99 giảm 3100 → 1700 ms.
- **Nút thắt khi chạy 1 instance là CPU của agent.** Container bị giới hạn `cpus: 0.5` và đã dùng 0.415 core (khoảng 83%, có lúc bị throttle). Latency của LLM gần như không đổi giữa 2 lượt (p50 khoảng 151 ms, p95 198 ms), nhưng latency HTTP thì cao gấp 2.4 lần (p50 365 so với 151 ms). Phần chênh đó là **thời gian xếp hàng** chờ event loop (in-flight lên tới 58). Chia ra 3 instance thì mỗi instance chỉ dùng 0.19 core, hết phải xếp hàng.
- **RPS tăng ít (+22%) vì giới hạn nằm ở phía load generator.** Locust là hệ thống closed-loop: 100 user, mỗi user nghỉ `between(0.5, 2)` (trung bình 1.25 s) giữa các request. Theo định luật Little, RPS tối đa ≈ 100 / (1.25 s + latency): 1 instance ≈ 100/(1.25+0.58) ≈ 55 req/s (đo được 52.8); 3 instance ≈ 100/(1.25+0.27) ≈ 66 (đo được 64.3). Ở 3 instance hệ thống **không còn bão hoà**. Muốn biết trần thật thì phải tăng số user.
- **Sau khi scale, nút thắt chuyển sang LLM:** p50 ở 3 instance (210 ms) gần bằng latency LLM cộng retrieval, và **p99 1700 ms vẫn còn** vì đó là 3% request mà upstream LLM chậm thêm 1.5 s. Thêm instance không làm LLM nhanh hơn. Muốn giảm tail phải xử lý ở phía LLM: timeout + retry/hedging, cache câu trả lời, model nhanh hơn, hoặc streaming để giảm thời gian chờ token đầu tiên.

---

## 6. Kết luận

- 3 điều bạn học được về benchmark & observability:
  1. **Average nói dối, percentile mới thật, và phải chọn đúng percentile.** Ở mục 2.2, `MOCK_SLOW_RATE=0.1` làm p95 tăng 6 lần trong khi avg vẫn "đạt SLO". Ở 4.0, p95 khác avg chỉ 54 ms nhưng p99 gấp 7.6 lần. Tỉ lệ request chậm < 5% thì p95 không thấy, chỉ p99 thấy.
  2. **Số đo chỉ có giá trị khi chắc chắn mình đo đúng thứ định đo.** Hai lần trong lab số liệu "trông hợp lý" nhưng sai: agent v2 chưa thật sự khởi động lại (bắt được nhờ `uptime_seconds`), và `docker compose run` âm thầm đổi số instance (bắt được nhờ metric theo instance và `docker events`). Luôn kiểm tra điều kiện thí nghiệm (PID, version, số target) **trước và sau** mỗi lần đo.
  3. **Ba trụ cột phải nối được với nhau, và mỗi tầng chỉ thấy phần của nó.** Dashboard báo p95 cao, Jaeger chỉ ra span nào chậm (và cả phần thời gian nằm *ngoài* mọi span là do xếp hàng), `trace_id` dẫn tới đúng dòng log. Nhưng metric của agent không thấy lỗi 504 ở Nginx (thí nghiệm D), và không metric hạ tầng nào thấy chất lượng tụt từ 100% xuống 4% (thí nghiệm E). Cần cả giám sát ở tầng load balancer/synthetic lẫn eval chất lượng.
- Nếu đưa agent này lên production thật, bạn sẽ thêm metric / alert nào nữa?
  - **Chất lượng:** alert `retrieval hit ratio < 0.8` và `absent()` cho metric retrieval; chạy eval định kỳ trên traffic thật (LLM-as-judge) và xuất `agent_eval_accuracy` thành gauge có alert; tỉ lệ feedback 👎.
  - **Tầng ngoài agent:** metric của Nginx/load balancer (tỉ lệ 5xx, gồm cả 502/504 do upstream) và **synthetic check** gọi `/ask` từ bên ngoài mỗi phút. Cả hai đều bắt được lỗi mà thí nghiệm D cho thấy metric của agent bỏ sót.
  - **SLO theo error budget burn rate** (multi-window, ví dụ 1 giờ và 6 giờ) thay cho ngưỡng cố định, để vừa bắt nhanh sự cố lớn vừa không báo động vì spike ngắn.
  - **Saturation:** CPU throttling của container (`container_cpu_cfs_throttled_seconds_total`) và độ trễ event loop, vì mục 5 cho thấy CPU mới là nút thắt.
  - **Cost:** sửa ngưỡng `LLMCostBurnRateHigh` theo chi phí nền thực tế (hiện $2/giờ nhỏ hơn mức bình thường $2.40/giờ), thêm alert theo % budget ngày, và cost tách theo `model` để bắt sớm việc đổi nhầm model (thí nghiệm C: chi phí tăng 15–22 lần so với mức nền, đúng tỉ lệ giá 16.7 lần).
  - **LLM upstream:** timeout và tỉ lệ retry của lời gọi LLM, latency tới token đầu tiên (TTFT) khi dùng streaming.
