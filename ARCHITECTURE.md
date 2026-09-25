# L3B Architecture Record

Team phải cập nhật tài liệu này cùng source. Mục tiêu là mô tả quyết định có thể kiểm chứng, không ghi prompt bí mật hoặc chain-of-thought.

## 1. System overview

Vẽ hoặc mô tả luồng từ input/candidate resolution đến MCP investigation, specialist agents, conflict resolver, verifier, output và trace.

```text
Input → Entity Resolver → Coordinator → Specialists → Conflict Resolver → Verifier → Output
            │                              │                  │             │
            └──────────────────────────── MCP ────────────────┴──────────── Trace
```

## 2. Agent ownership

| Actor | Input | Trách nhiệm | Tool permission | Output/handoff |
| --- | --- | --- | --- | --- |
| Entity/customer | TODO | TODO | TODO | TODO |
| Coordinator | TODO | TODO | TODO | TODO |
| Order/product | TODO | TODO | TODO | TODO |
| Shipment | TODO | TODO | TODO | TODO |
| Payment/refund | TODO | TODO | TODO | TODO |
| Policy | TODO | TODO | TODO | TODO |
| Conflict resolver | TODO | TODO | TODO | TODO |
| Verifier | TODO | TODO | TODO | TODO |

Áp dụng least privilege; tool discovery không đồng nghĩa mọi actor đều được gọi mọi tool.

## 3. Entity resolution và A2A protocol

Mô tả cách xếp hạng/reject candidate, confidence threshold, message envelope, correlation theo `case_id`, điều kiện handoff, timeout và cách tránh vòng lặp. Không trace nội dung suy luận riêng.

## 4. Evidence và conflict lifecycle

Mô tả cách validate MCP response, lưu `evidence_ref`, chọn source theo policy, biểu diễn unresolved conflict, map evidence vào claim/output và emit `tool_result_consumed`. Evidence không được tái sử dụng giữa các case.

## 5. Failure and efficiency policy

| Failure | Retry budget | Fallback | Trace event/code |
| --- | ---: | --- | --- |
| MCP timeout | TODO | TODO | TODO |
| Entity not found/ambiguous | TODO | TODO | TODO |
| Source conflict | TODO | TODO | TODO |
| Invalid specialist result | TODO | TODO | TODO |

Nêu query budget/cache strategy để tránh gọi lặp và quét rộng. Retry phải có giới hạn, idempotent và không biến missing evidence thành dữ liệu phỏng đoán.

## 6. Verification invariants

Liệt kê kiểm tra trước finalize: schema, entity scope, rejected candidates, evidence ownership, claim linkage, timeline, payment/refund totals, source precedence, responsibility/action consistency và confidence bounds.

## 7. Reproducibility

Hệ thống được thiết kế ưu tiên tính tất định để đảm bảo kết quả chấm điểm nhất quán trên mọi môi trường.

*   **Model & Config:**
    *   Mô hình AI: `[Điền phiên bản model cụ thể, vd: gemini-1.5-flash-002]` (Khóa cứng phiên bản, tuyệt đối không dùng tag `latest`).
    *   Tham số suy luận: Bắt buộc `temperature = 0.0` trên toàn bộ Specialist Agents và Verifier để giảm thiểu ảo giác và giữ tính logic cố định.
    *   Bảo mật: Không đính kèm API Key. Giám khảo sử dụng file `.env.example` để tạo file `.env` cục bộ.
*   **Dependency Pinning:** Toàn bộ thư viện môi trường được khóa cứng phiên bản tại `pyproject.toml`. 
*   **Concurrency & Resource Limits:** 
    *   Giới hạn đồng thời: Sử dụng `asyncio.Semaphore(5)` (hoặc tương đương) tại Coordinator để chặn gọi API ồ ạt, tránh dính lỗi Rate Limit (HTTP 429).
    *   Tài nguyên tối thiểu: Python 3.10+, RAM 4GB.
*   **Random Seed:** Cố định `random.seed(42)` ngay tại điểm khởi chạy (Entry point) của `cli.py`.
*   **Lệnh chạy kiểm chứng:**
    *   Cài đặt: `pip install -e .`
    *   Thực thi luồng chính: `python -m src.student_agent.cli --input inputs/ --output outputs/`
    *   Kiểm thử an toàn: `pytest tests/test_release_safety.py -v`
