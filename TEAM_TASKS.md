# Day09 L3B Multi-Agent: Bảng Phân Công & Theo Dõi Tiến Độ Nhóm

Tài liệu này dùng để điều phối công việc giữa 3 thành viên: **Hải**, **Hoàng**, và **Đại**.

---

## 1. Trạng Thái Hiện Tại (Current Status)

* **Hải (Quality Gatekeeper)**: **ĐÃ HOÀN THÀNH 100% CỐT LÕI (Task 1.2 & Task 2.2)**
  * Đã xây dựng `ConflictResolver` (Source Precedence: Carrier > Seller > Customer; Gateway > Invoice > Customer).
  * Đã xây dựng `Verifier` kiểm tra chặt chẽ 10 Invariants bằng Pure Python (không bị ảo giác, không tốn token, latency 0ms).
  * Đã xây dựng `Output Builder` (`build_l3b_output`) chuẩn JSON Schema `l3b-output-v2.schema.json`.
  * Đã tích hợp luồng vào `workflow.py` và đang chạy thực tế trên 100 cases thật.

---

## 2. Checklist Công Việc Dành Cho HOÀNG

**Trách nhiệm chính:** *Coordinator, Specialists Prompt Engineering & LLM Integration (Model < 10B)*

* [ ] **Task H1: Tinh chỉnh A2A Dispatching & Round-Budget**
  * **File:** `src/student_agent/workflow.py`
  * **Mục tiêu:** Quản lý vòng lặp A2A giữa Coordinator và các Specialists.
  * **Yêu cầu:**
    * Giữ round-budget tối đa 1-2 vòng gọi Specialists (không lặp vô tận).
    * Áp dụng `asyncio.Semaphore(5)` để chặn gọi API dồn dập tránh lỗi HTTP 429.
    * Đảm bảo message envelope mang đủ `case_id`, `from`, `to`, `type`, `payload`.

* [ ] **Task H2: Tối ưu hóa System Prompt cho 4 Specialist Agents**
  * **File:** `src/student_agent/llm.py`, `src/student_agent/workflow.py`
  * **Mục tiêu:** Nâng cao chất lượng suy luận ngữ nghĩa của các Specialist khi chạy với model `< 10B` (như `qwen2.5:7b` qua Ollama hoặc `qwen/qwen3.5-9b` qua OpenRouter).
  * **Yêu cầu:**
    * Viết prompt ngắn gọn, tập trung đúng domain:
      * *Order Specialist*: Đọc `get_order`, `get_order_items`, trích xuất đúng item, seller, ngày mua.
      * *Shipment Specialist*: Đọc `get_shipment_summary`, so khớp timeline giao hàng với `shipping_limit_date` và `estimated_delivery_at`.
      * *Payment Specialist*: Đọc `get_order_payments`, tính toán tổng số tiền đã thu, đối soát các phương thức thanh toán (`credit_card`, `boleto`, `voucher`).
      * *Policy Specialist*: Đọc `get_policy`, tra cứu đúng điều khoản đền bù theo `policy_version`.
    * Cố định tham số `temperature = 0.0` trên toàn bộ Specialist.

* [ ] **Task H3: Xử lý Semantic Reconciliation (Khiếu nại đa chủ đề)**
  * **File:** `src/student_agent/workflow.py`
  * **Yêu cầu:** Xử lý các case khách khiếu nại phức tạp (vừa giao trễ vừa đòi hủy đơn hoàn tiền). Đảm bảo Coordinator phân biệt được nguyên nhân gốc rễ (`primary_issue`) và các nguyên nhân phụ (`secondary_issues`).

---

## 3. Checklist Công Việc Dành Cho ĐẠI

**Trách nhiệm chính:** *Entity Resolver, MCP Gateway, Traces & Đóng Gói Nộp Bài*

* [ ] **Task D1: Hoàn thiện Entity Resolver (Confidence Scoring & Provenance)**
  * **File:** `src/student_agent/workflow.py`
  * **Mục tiêu:** Giải quyết triệt để các case không có exact order ID hoặc có nhiều candidate.
  * **Yêu cầu:**
    * Viết hàm chấm điểm confidence (0.0 - 1.0) cho từng candidate dựa trên mức độ trùng khớp ngày mua, giá trị đơn, sản phẩm từ `get_customer_history`.
    * **Tuyệt đối không xóa âm thầm candidate**: Toàn bộ candidate không được chọn bắt buộc phải đưa vào danh sách `rejected_candidates` (đáp ứng Invariant 3 của Hải).

* [ ] **Task D2: Giám sát Cache & Bảo vệ điểm Efficiency (5%)**
  * **File:** `src/student_agent/mcp_gateway.py`
  * **Mục tiêu:** Không bị trừ điểm hiệu năng gọi tool.
  * **Yêu cầu:**
    * Kiểm tra bộ nhớ cache `(case_id, tool_name, params)` trong `EvidenceGateway`.
    * Đảm bảo mỗi specialist chỉ gọi đúng 1-2 tool cần thiết trong domain của mình, không gọi chéo domain (least privilege).
    * Gọi `gateway.clear_case_cache(case_id)` khi hoàn tất mỗi case.

* [ ] **Task D3: Kiểm tra Trace Hợp Lệ & Bảo mật**
  * **File:** `traces/trace.jsonl`
  * **Mục tiêu:** Đáp ứng 100% tiêu chí `workflow` và `provenance`.
  * **Yêu cầu:**
    * Đảm bảo trace chỉ ghi 5 sự kiện hợp lệ: `case_received`, `task_assigned`, `tool_result_consumed`, `handoff`, `verification_completed`, `case_finalized`.
    * **Tuyệt đối không ghi prompt nội bộ, chain-of-thought, hay `sk-team-...` API key vào file trace**.

* [ ] **Task D4: Chạy Thẩm Định & Đóng Gói Nộp Bài**
  * **Lệnh kiểm tra:** `python -m student_agent.cli validate`
    * Kiểm tra đầu ra: Đảm bảo in `OK: 100 outputs / X trace events` và không có lỗi nào.
  * **Lệnh đóng gói:** `python -m student_agent.cli package --output dist/submission.zip`
    * Kiểm tra file zip `dist/submission.zip` chỉ gồm 3 thành phần: `manifest.json`, `trace.jsonl`, `outputs/*.json`.
    * Đảm bảo dung lượng zip < 12MB.

---

## 4. Công Việc Còn Lại Của HẢI (Đang Theo Dõi)

* [ ] **Task 3.2: Bắt lỗi và hoàn thiện Edge Cases khi chạy 100 cases**
  * Giám sát kết quả chạy của 100 cases.
  * Nếu Verifier chặn lại ở case nào (ví dụ: làm tròn tiền, candidate dị biệt), Hải trực tiếp debug và thêm logic an toàn để 100/100 outputs đều hợp lệ.
* [ ] **Task 3.3: Phối hợp kiểm tra chốt chặn cuối cùng cùng Đại trước khi submit**.
