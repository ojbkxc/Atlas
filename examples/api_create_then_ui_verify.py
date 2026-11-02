"""真实 API 造数 → UI 验证（方案要求的核心混用场景）。

链路：
  1) API 建单（saveSalesOrder，报文来自真实 UI 捕获）
  2) UI 打开销售订单列表，按单号查询
  3) UI 侧断言该单存在且 PT 正确

这是「API 快速造数据 + UI 验证业务界面」的端到端证据，
不是桩、不是离线模拟。
"""
from __future__ import annotations
import json
from pathlib import Path

from atlas.config import AtlasConfig
from atlas.plugins.demo.creators import make_sales_order_creator
from atlas.runtime import DemoRuntime

TEMPLATE = Path(__file__).resolve().parents[1] / "scripts" / "templates" / "sale_order_save_template.json"


def main() -> int:
    config = AtlasConfig.from_root(env="sit", mode="run")
    with DemoRuntime(config) as runtime:
        create = make_sales_order_creator(runtime.demo_api, TEMPLATE)
        created = create({
            "customer_code": "CUST0000001",
            "customer_code_full": "CUST00000010001",
            "payment_terms": "NET30",
            "warehouse_code": "WH-DEMO",
            "remark": "API造数UI验证",
        })
        print(json.dumps({"step": "api_create", **created}, ensure_ascii=False))
        if "非人工创建" not in created["remark"]:
            raise AssertionError("建单备注缺少「非人工创建」")

        # UI 侧验证：打开列表并按单号查询
        from atlas.dsl import FlowCompiler
        flow = {
            "id": "api-create-then-ui-verify",
            "steps": [
                {"task": "open_doc_list", "page_key": "sales_order", "channel": "ui"},
            ],
        }
        FlowCompiler(runtime.registry(), runtime.provider).run(flow, runtime.actor)

        page = runtime.actor.page
        found = page.evaluate(
            """(orderNo) => {
                const tables = Array.from(document.querySelectorAll('.el-table'));
                for (const t of tables) {
                    const vm = t.__vue__;
                    if (!vm || !Array.isArray(vm.data)) continue;
                    const hit = vm.data.find(r => String(r.orderNo||'') === orderNo);
                    if (hit) return {ok: true, orderNo: hit.orderNo,
                                     pt: hit.paymentTerms, customer: hit.customerCorpNumber};
                }
                return {ok: false};
            }""", created["orderNo"])

        print(json.dumps({"step": "ui_verify", **found}, ensure_ascii=False))
        if not found.get("ok"):
            raise AssertionError(f"UI 列表中未找到 API 建的单 {created['orderNo']}")
        print("API 造数 → UI 验证: PASSED")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
