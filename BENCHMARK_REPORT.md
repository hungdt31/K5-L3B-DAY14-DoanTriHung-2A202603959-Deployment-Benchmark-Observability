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

> _Phần này làm ở CP8 (Part 7 Bước 2, sau giờ lab)._

### 4.1 Screenshot

- [ ] Dashboard Grafana khi đang có tải (thấy rõ Golden Signals)
- [ ] Một trace chậm trong Jaeger (thấy span nào chiếm thời gian)
- [ ] Một trace lỗi trong Jaeger (có event `exception`)
- [ ] Trang Alerts của Prometheus với ít nhất 1 alert đang **firing**

### 4.2 Thí nghiệm "phá hệ thống"

| # | Thay đổi | Alert nào firing | Thời gian từ lúc đổi → firing | Panel nào phản ánh rõ nhất |
|---|---|---|---|---|
| A | `MOCK_ERROR_RATE=0.2` | | | |
| B | `MOCK_SLOW_RATE=0.3` | | | |
| C | `LLM_MODEL=mock-llm-large` | | | |
| D | `docker pause` 1 instance | | | |
| E | `AGENT_VERSION=v1` | | | |

**Thí nghiệm E không làm alert nào firing. Bạn sẽ phát hiện loại sự cố này trong production bằng cách nào?**

...

### 4.3 Debug bằng trace

Chọn 1 trace chậm nhất bạn tìm được:

- `trace_id`: ...
- Tổng thời gian: ... ms
- Span chiếm nhiều thời gian nhất: ... (... ms, ...%)
- Dòng log tương ứng (grep theo `trace_id`):

```json

```

---

## 5. Benchmark scaling (production, Locust headless, 100 users, 2 phút)

> _CP8._

| Số instance | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Failures |
|---|---|---|---|---|---|
| 1 | | | | | |
| 3 | | | | | |

**Giải thích kết quả:** Scale có cải thiện p95 không? Nút thắt nằm ở đâu?

...

---

## 6. Kết luận

> _Hoàn thiện ở CP8._

- 3 điều bạn học được về benchmark & observability:
  1. ...
  2. ...
  3. ...
- Nếu đưa agent này lên production thật, bạn sẽ thêm metric / alert nào nữa?

...
