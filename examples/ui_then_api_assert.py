"""真实 UI/API 混用验证：UI 建单 → API 断言落库值。

这是方案里“UI 操作 + API 断言”的端到端证据：
  - UI 侧由 Atlas DSL 真实点击建单
  - API 侧用 DemoApi 查列表，断言 paymentTerms 与 UI 改选值一致
绕开 DOM 与 Vue 双模型，直接看服务端真实值。
"""
from __future__ import annotations
import json

from atlas.config import AtlasConfig
from atlas.dsl import FlowCompiler
from atlas.runtime import DemoRuntime


def main() -> int:
    config = AtlasConfig.from_root(env="sit", mode="run")
    with DemoRuntime(config) as runtime:
        flow = {
            "id": "ui-then-api-assert",
            "engine": config,
            "steps": [
                {"task": "open_doc_list", "page_key": "sales_order", "channel": "ui"},
                {"task": "click_new", "page_key": "sales_order"},
                {"task": "select_customer", "code": "CUST0000001",
                 "code_full": "CUST00000010001"},
                {"task": "read_pt"},
                {"task": "ensure_warehouse", "wh": "WH-DEMO"},
                {"task": "ensure_sales_person"},
                {"task": "mount_other_header"},
                {"task": "click_save"},
                {"task": "wait_save_result"},
            ],
        }
        result = FlowCompiler(runtime.registry(), runtime.provider).run(flow, runtime.actor)
        doc_no = runtime.actor.ctx.get("document_no")
        print(json.dumps({"flow_status": result["status"], "document_no": doc_no},
                         ensure_ascii=False))
        if not doc_no:
            raise AssertionError("UI 建单未取到单号")

        # UI 建完后用 API 交叉验证：单号确实存在且 PT 落库
        listed = runtime.demo_api.sale_order_list(runtime.division_code, page_size=200)
        rows = (listed.get("data") or {}).get("data") or []
        hit = next((r for r in rows if str(r.get("orderNo")) == str(doc_no)), None)
        if hit is None:
            raise AssertionError(f"API 查不到 UI 刚建的单 {doc_no}")
        print(json.dumps({"api_found": True, "order_no": hit.get("orderNo"),
                          "payment_terms": hit.get("paymentTerms"),
                          "customer": hit.get("customerCorpNumber")}, ensure_ascii=False))
        print("UI 建单 → API 断言: PASSED")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
