# L3B Architecture Record

Team phải cập nhật tài liệu này cùng source. Mục tiêu là mô tả quyết định có thể kiểm chứng, không ghi prompt bí mật hoặc chain-of-thought.

## 1. System overview

Vẽ hoặc mô tả luồng từ input/candidate resolution đến MCP investigation, specialist agents, conflict resolver, verifier, output và trace.

```text
Input → Entity Resolver → Coordinator → Specialists → Conflict Resolver → Verifier → Output
            │                              │                  │             │
            └──────────────────────────── MCP ────────────────┴──────────── Trace
```

**Mô tả luồng:**

1. **Input** — case JSON tại `inputs/L3B_CASE_XXX.json`. Một số case không có `order_id` chính xác, chỉ có mô tả khiếu nại + gợi ý (tên khách, khoảng ngày, sản phẩm...).
2. **Entity Resolver** — gọi các MCP search/lookup tool (tên tool xác định qua `day09 mcp-tools` tại runtime, không hard-code/đoán tên) để dựng danh sách candidate order + customer, chấm confidence, loại candidate dưới ngưỡng nhưng **vẫn lưu lại** kèm lý do (phục vụ tiêu chí `evidence`/`consistency` và §6 bên dưới).
3. **Coordinator** — nhận entity đã resolve (candidate top-1 + confidence), suy ra loại khiếu nại từ case description để quyết định specialist nào cần huy động, phát A2A message `task_assigned`, gọi các specialist độc lập song song (qua `asyncio.gather` bên trong `asyncio.Semaphore(5)`) thay vì tuần tự, và giữ round-budget/timeout cứng để chặn vòng lặp (chi tiết §3, §5).
4. **Specialists** (Order/Product, Shipment, Payment/Refund, Policy) — mỗi agent chỉ được gọi MCP tool trong đúng domain của mình (least privilege), trả về claim có cấu trúc kèm `evidence_ref` gốc từ MCP gateway, không suy diễn ngoài evidence đã lấy.
5. **Conflict Resolver** — so khớp claim giữa các specialist và giữa các nguồn evidence, áp dụng source precedence (§4); nếu không giải quyết được thì đánh dấu `unresolved_conflict` thay vì tự chọn một phía.
6. **Verifier** — chạy toàn bộ invariant ở §6 trước khi cho phép finalize. Case fail invariant bị trả về (hoặc hạ confidence + note) thay vì được "vá" bằng suy đoán.
7. **Output/Trace** — `outputs/<case_id>.json` theo JSON Schema chấm điểm; `traces/trace.jsonl` chỉ ghi sự kiện quan sát được (`task_assigned`, `handoff`, `tool_result_consumed`, `verification_completed`) — không ghi prompt nội bộ hay chain-of-thought của agent.

**Nguyên tắc chống bùng nổ vòng lặp (bổ sung):** hệ thống không cấp quyền tự-sửa-lỗi hay tự-tranh-luận vô hạn cho bất kỳ agent nào (tinh thần "Agentless" — quyết định qua quy trình tuyến tính thay vì để agent tự do lặp). Mỗi agent chỉ nhận đúng lượng ngữ cảnh tối thiểu cho một bước xử lý (extreme decomposition), và việc dừng vòng lặp luôn do round-budget cứng ở §3/§5 quyết định — không dựa vào agent tự nhận biết khi nào nên dừng.

## 2. Agent ownership

| Actor | Input | Trách nhiệm | Tool permission | Output/handoff |
| --- | --- | --- | --- | --- |
| Entity/customer | Case description, gợi ý entity thô (tên/email/order_id một phần, khoảng ngày) | Sinh & xếp hạng candidate order/customer, tính confidence, loại candidate dưới threshold kèm lý do | MCP entity/customer lookup & search tools (đã xác nhận: `get_customer_history`; các tool khác lấy qua tool discovery) | Gửi entity đã resolve (top-1 + confidence + rejected list) cho Coordinator |
| Coordinator | Entity đã resolve, case metadata (`case_id`, loại khiếu nại) | Định tuyến specialist theo loại khiếu nại, phát `task_assigned`, quản lý round-budget/timeout, gom kết quả chuyển cho Conflict Resolver. Thiết kế **stateless** — không lưu state giữa các case, mọi context cần thiết đi kèm trong message | Chỉ có quyền tool discovery + A2A dispatch — **không** gọi trực tiếp MCP evidence tool theo domain (tránh double-fetch, giữ least privilege) | `task_assigned`/`handoff` tới specialist tương ứng kèm `case_id` + entity context |
| Order/product | `order_id`/`product_id` đã resolve, nội dung khiếu nại liên quan đơn hàng/sản phẩm | Xác minh thông tin đơn hàng, sản phẩm, seller so với claim của khách | MCP tool domain order/product (qua tool discovery) | Claim có cấu trúc + `evidence_ref` → Coordinator/Conflict Resolver |
| Shipment | `order_id` đã resolve, khiếu nại liên quan giao hàng | Xác minh trạng thái và timeline vận chuyển | MCP tool domain shipment (qua tool discovery) | Claim + `evidence_ref` → Coordinator/Conflict Resolver |
| Payment/refund | `order_id` đã resolve, khiếu nại liên quan thanh toán/hoàn tiền | Xác minh giao dịch thanh toán, trạng thái refund, đối chiếu số tiền | MCP tool domain payment/refund (qua tool discovery) | Claim + `evidence_ref` → Coordinator/Conflict Resolver |
| Policy | Loại khiếu nại + claim từ specialist khác khi cần căn cứ chính sách | Cung cấp điều khoản áp dụng (thời hạn đổi trả, điều kiện hoàn tiền...) để Conflict Resolver/Verifier đối chiếu | MCP policy lookup tool (qua tool discovery) | Policy reference + claim → Conflict Resolver |
| Conflict resolver | Toàn bộ claim + `evidence_ref` từ các specialist | Phát hiện source conflict, áp dụng source precedence, đánh dấu `unresolved_conflict` nếu không giải quyết được. **Không gọi LLM** — xử lý hoàn toàn bằng bảng ưu tiên nguồn tĩnh (rule-based lookup), không dùng multi-agent debate/voting | Không gọi MCP mới — chỉ xử lý trên evidence đã thu thập trong case (giữ hiệu quả, tránh gọi thừa) | Resolved claim set (+ `unresolved_conflicts` nếu có) → Verifier |
| Verifier | Resolved claim set, toàn bộ `evidence_ref`, draft output | Chạy invariant §6; reject/trả về nếu fail; finalize + emit `verification_completed` nếu pass. **Không gọi LLM** — triển khai bằng code tất định (schema validation, so sánh số học, kiểm tra thứ tự thời gian, tra bảng enum hợp lệ cho trách nhiệm/action) | Read-only trên evidence đã có — không gọi MCP mới | `outputs/<case_id>.json` cuối cùng + trace `verification_completed` |

Áp dụng least privilege; tool discovery không đồng nghĩa mọi actor đều được gọi mọi tool. Cụ thể: chỉ các specialist và Entity Resolver được gọi MCP evidence tool theo đúng domain của mình; Coordinator, Conflict Resolver và Verifier không tự fetch evidence mới — điều này vừa giảm số MCP call trùng lặp (tiêu chí `efficiency`), vừa giữ trace `tool_result_consumed` phản ánh đúng ai thật sự tiêu thụ evidence nào (tiêu chí `provenance`).

## 3. Entity resolution và A2A protocol

Mô tả cách xếp hạng/reject candidate, confidence threshold, message envelope, correlation theo `case_id`, điều kiện handoff, timeout và cách tránh vòng lặp. Không trace nội dung suy luận riêng.

- **Sinh candidate:** Entity Resolver gọi MCP search/lookup tool (customer theo tên/email, order theo khoảng ngày + giá trị + mô tả sản phẩm) để dựng tập candidate ban đầu.
- **Chấm điểm:** mỗi candidate nhận confidence 0–1 theo trọng số trường khớp — `customer_unique_id`/`order_id` khớp chính xác có trọng số cao nhất, sau đó đến ngày mua, giá trị đơn, mô tả sản phẩm.
- **Ngưỡng reject:** candidate dưới ngưỡng tối thiểu (ví dụ confidence < 0.5) bị loại khỏi luồng chính nhưng **vẫn được lưu lại** trong `rejected_candidates` kèm lý do — không xoá âm thầm, phục vụ invariant "rejected candidates" ở §6.
- **Trường hợp mơ hồ:** nếu candidate top-1 không có margin rõ rệt so với top-2 (chênh lệch confidence nhỏ), case được gắn `entity_ambiguous` và đi theo fallback ở §5 thay vì Coordinator tự chọn đại.
- **A2A message envelope:** JSON tối thiểu gồm `case_id`, `from`, `to`, `type` (`task_assigned` | `handoff` | `result`), `payload` (entity context hoặc claim). `correlation_id = case_id` bắt buộc trên mọi message trong một case, để mọi handoff truy vết đúng case và không lẫn evidence/claim giữa các case.
- **Điều kiện handoff:** Coordinator chỉ handoff sang specialist khi entity đã resolve với confidence ≥ ngưỡng. Nếu Entity Resolver không resolve được, case bị gắn `unresolved_entity` và Verifier từ chối finalize ở mức confidence cao theo policy §5 — không có specialist nào được gọi trên một entity chưa chắc chắn.
- **Timeout & chống vòng lặp:** mỗi handoff có timeout riêng theo từng MCP call; Coordinator giữ round-budget cố định (tối đa 1 vòng gọi song song toàn bộ specialist liên quan + tối đa 1 vòng follow-up nếu Conflict Resolver cần thêm bằng chứng cụ thể). Hết round-budget mà vẫn chưa đủ điều kiện finalize thì case chuyển sang nhánh "insufficient evidence" thay vì Coordinator tiếp tục lặp vô hạn.

## 4. Evidence và conflict lifecycle

Mô tả cách validate MCP response, lưu `evidence_ref`, chọn source theo policy, biểu diễn unresolved conflict, map evidence vào claim/output và emit `tool_result_consumed`. Evidence không được tái sử dụng giữa các case.

- **Validate MCP response:** mọi response phải có `evidence_ref` do MCP gateway cấp; agent không được tự tạo, sửa, hoặc suy diễn `evidence_ref` — chỉ dùng nguyên bản giá trị trả về.
- **Lưu `evidence_ref`:** gắn ở mức **claim field**, không gắn chung chung cho toàn bộ output — mỗi kết luận trong output có mảng `evidence_refs` riêng trỏ đúng bằng chứng hỗ trợ nó (phục vụ tiêu chí `provenance` và `claim linkage` ở §6).
- **Chọn nguồn theo policy (source precedence):** định nghĩa thứ tự ưu tiên rõ ràng cho các câu hỏi hay xung đột, ví dụ: shipment tracking log > seller-reported status > lời khai khách hàng khi xác định "đã giao hay chưa"; payment gateway record > refund request form khi xác định số tiền hoàn thực tế. Bảng ưu tiên này được **mã hoá cứng dưới dạng rule/lookup table trong code**, Conflict Resolver tra bảng thay vì gọi LLM để "cân nhắc" — loại bỏ rủi ro agent tự nguỵ biện lý do chọn nguồn.
- **Biểu diễn unresolved conflict:** field riêng `unresolved_conflicts` trong output, mỗi mục mô tả claim mâu thuẫn kèm `evidence_refs` của cả hai phía. Verifier chặn finalize ở confidence cao nếu unresolved conflict ảnh hưởng trực tiếp tới kết luận trách nhiệm/action mà không có giải trình.
- **Map evidence → claim/output:** không dùng một `evidence_ref` "gốc" áp cho toàn bộ case; mỗi claim quan trọng phải tự mang evidence của riêng nó.
- **Emit `tool_result_consumed`:** phát ngay khi một specialist thực sự **dùng** evidence trong claim cuối cùng — không phát cho mọi evidence đã fetch nhưng bị bỏ qua, để trace phản ánh đúng evidence nào thật sự đóng góp vào kết luận.
- **Không tái sử dụng chéo case:** evidence được cache/lưu theo scope `(case_id, tool_name, params)`; khi case kết thúc, cache bị bỏ, không mang evidence hoặc `evidence_ref` sang case khác dù cùng entity xuất hiện ở nhiều case.

## 5. Failure and efficiency policy

| Failure | Retry budget | Fallback | Trace event/code |
| --- | ---: | --- | --- |
| MCP timeout | Tối đa 2 lần, backoff ngắn, idempotent | Nếu vẫn timeout: specialist trả claim ở confidence thấp kèm note `evidence_unavailable`; Coordinator tiếp tục các specialist khác thay vì chặn toàn case | `mcp_timeout_retry`, `mcp_call_failed` |
| Entity not found/ambiguous | Không retry lặp query giống hệt; tối đa 1 lần mở rộng query (thêm biến thể tên/khoảng ngày) | Case đi theo nhánh confidence thấp, đề xuất action "cần xác minh thủ công thêm"; `rejected_candidates` vẫn được liệt kê đầy đủ | `entity_unresolved` |
| Source conflict | Không retry MCP (dữ liệu đã có sẵn) | Conflict Resolver áp source precedence (§4); nếu vẫn mâu thuẫn → ghi `unresolved_conflict` và hạ confidence tổng thể tương ứng | `conflict_detected`, `conflict_unresolved` |
| Invalid specialist result (thiếu `evidence_ref`/sai schema) | Tối đa 1 lần yêu cầu specialist tự sửa định dạng (không gọi MCP mới) | Nếu vẫn invalid: Verifier loại claim đó khỏi output, đánh dấu field liên quan là "incomplete" thay vì đoán giá trị | `verification_failed`, `claim_rejected` |

**Query budget/cache:** mỗi case có ngân sách MCP call tối đa cố định theo domain (ví dụ 1–2 call/specialist, chỉ vượt khi Conflict Resolver yêu cầu follow-up có lý do cụ thể). Coordinator và specialist cache kết quả trong bộ nhớ theo key `(case_id, tool_name, params)` để không gọi lặp cùng một truy vấn trong cùng case; cache bị xoá khi case đóng, tránh quét rộng ngoài phạm vi cần thiết.

**Nguyên tắc retry:** mọi retry phải idempotent (không đổi state phía server), có giới hạn cứng như bảng trên, và **không bao giờ** dùng giá trị suy đoán để lấp evidence còn thiếu — nếu hết retry mà vẫn thiếu, field liên quan phải được note rõ "evidence không đủ" thay vì điền số liệu phỏng đoán.

## 6. Verification invariants

Liệt kê kiểm tra trước finalize: schema, entity scope, rejected candidates, evidence ownership, claim linkage, timeline, payment/refund totals, source precedence, responsibility/action consistency và confidence bounds.

Verifier chạy tuần tự các invariant sau; bất kỳ invariant nào fail thì case bị trả về (không tự sửa bằng suy đoán), Verifier emit `verification_completed` với `status=failed` kèm lý do. Toàn bộ 10 invariant dưới đây được triển khai bằng **code tất định** (validation, so sánh số học/thời gian, tra bảng enum) — không có bước nào giao cho LLM "chấm điểm" hay "đánh giá" chủ quan, để cùng một output luôn cho cùng một kết quả pass/fail:

1. **Schema** — output khớp JSON Schema bắt buộc (field, type, enum) trước khi ghi ra `outputs/`.
2. **Entity scope** — mọi `evidence_ref` dùng trong case phải thuộc đúng `case_id` và đúng entity (customer/order) đã resolve, không lẫn evidence ngoài phạm vi.
3. **Rejected candidates** — danh sách candidate bị loại ở §3 phải có lý do rõ ràng và xuất hiện trong output theo đúng schema yêu cầu.
4. **Evidence ownership** — `evidence_ref` phải do MCP gateway cấp cho đúng team/run/case hiện tại; evidence thuộc team/run/case khác bị coi là fail (theo tiêu chí 0 điểm của scorer).
5. **Claim linkage** — mọi claim quan trọng trong kết luận có ít nhất một `evidence_ref` hợp lệ đi kèm; không có claim "trôi nổi" thiếu bằng chứng.
6. **Timeline** — các mốc thời gian (đặt hàng, giao hàng, khiếu nại, hoàn tiền) phải theo thứ tự hợp lý; lệch thứ tự (ví dụ ngày hoàn tiền trước ngày giao hàng) phải bị gắn cờ trước khi finalize.
7. **Payment/refund totals** — số tiền hoàn phải khớp hoặc nhỏ hơn/bằng số tiền thanh toán gốc theo evidence; sai lệch phải được giải thích rõ trong output, không được bỏ qua.
8. **Source precedence** — khi có nguồn mâu thuẫn, output phải phản ánh đúng thứ tự ưu tiên đã định nghĩa ở §4, không chọn tuỳ tiện theo agent nào trả lời trước.
9. **Responsibility/action consistency** — kết luận trách nhiệm phải nhất quán với action đề xuất, kiểm tra qua **bảng tra cặp (responsibility, action) hợp lệ** cố định trong code (ví dụ: nếu trách nhiệm thuộc vận chuyển thì action không thể đề xuất theo hướng lỗi sản phẩm) — không để LLM tự phán đoán "có hợp lý không".
10. **Confidence bounds** — confidence tổng thể phải phản ánh đúng chất lượng evidence: nhiều evidence mạnh và không conflict → confidence cao; có `unresolved_conflict` hoặc evidence thiếu → confidence phải hạ tương ứng, không được báo cao tuỳ tiện.

## 7. Reproducibility

Hệ thống được thiết kế ưu tiên tính tất định để đảm bảo kết quả chấm điểm nhất quán trên mọi môi trường.

*   **Model & Config:**
    *   Mô hình AI: `qwen/qwen3.5-9b` (Qwen3.5-9B, 9B tham số — khoá cứng phiên bản này, tuyệt đối không dùng tag `latest`). Đây là model dưới ngưỡng 10B tham số có hỗ trợ native tool calling + reasoning + structured output mạnh nhất hiện có trên OpenRouter tính đến thời điểm viết tài liệu này, phù hợp cả cho Coordinator (định tuyến/điều phối) lẫn Specialist Agents (đọc evidence, sinh claim có cấu trúc). Nên chạy thử trên một tập case mẫu trước khi khoá version chính thức để xác nhận độ ổn định tool-calling thực tế.
    *   Reasoning budget: giới hạn effort suy luận theo vai trò để tránh loop kéo dài — Coordinator dùng reasoning effort cao hơn (quyết định định tuyến); Entity Resolver và Specialist Agent dùng effort thấp/tắt thinking cho tác vụ tra cứu đơn giản, giữ latency thấp cho từng round. Conflict Resolver và Verifier **không gọi LLM** (xem §2, §6) nên không cần cấu hình reasoning cho hai vai trò này.
    *   Tham số suy luận: Bắt buộc `temperature = 0.0` trên mọi lời gọi LLM còn lại (Entity Resolver, Coordinator, Specialist Agents) để giảm thiểu ảo giác và giữ tính logic cố định. Conflict Resolver và Verifier không dùng LLM nên tham số này không áp dụng — hai vai trò này đã tất định 100% theo thiết kế (code thuần), thay vì chỉ "gần tất định" nhờ temperature thấp.
    *   Structured Outputs: mọi claim do Specialist Agent sinh ra bắt buộc dùng chế độ structured output/JSON-schema-constrained generation (được validate lại bằng Pydantic phía client) thay vì parse văn bản tự do bằng regex — loại bỏ lỗi parsing và giữ format ổn định cho Conflict Resolver/Verifier tiêu thụ.
    *   Giao tiếp MCP: hệ thống chỉ đóng vai trò **MCP client**, gọi tới MCP Gateway do ban tổ chức host qua HTTPS (biến `MCP_ENDPOINT` trong `.env`, mọi call bị audit phía server theo team/case). Team **không** tự dựng MCP server hay chọn transport (stdio/in-memory/HTTP) — quyết định transport thuộc về gateway của ban tổ chức, không phải phía client.
    *   Bảo mật: Không đính kèm API Key. Giám khảo sử dụng file `.env.example` để tạo file `.env` cục bộ.
*   **Dependency Pinning:** Toàn bộ thư viện môi trường được khóa cứng phiên bản tại `pyproject.toml`.
*   **Concurrency & Resource Limits:**
    *   Giới hạn đồng thời: Sử dụng `asyncio.Semaphore(5)` (hoặc tương đương) tại Coordinator để chặn gọi API ồ ạt, tránh dính lỗi Rate Limit (HTTP 429).
    *   Tài nguyên tối thiểu: Python 3.11+, RAM 4GB.
*   **Random Seed:** Cố định `random.seed(42)` ngay tại điểm khởi chạy (Entry point) của `cli.py`.
*   **Lệnh chạy kiểm chứng:**
    *   Cài đặt: `pip install -e ".[dev]"`
    *   Thực thi luồng chính: `python -m src.student_agent.cli --input inputs/ --output outputs/`
    *   Kiểm thử an toàn: `pytest tests/test_release_safety.py -v`
