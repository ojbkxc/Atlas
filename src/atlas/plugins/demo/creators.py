"""真实 API 建单（进程内）：复用 Atlas 已登录的 DemoApi 会话。

为什么进程内而不是子进程：建单需要浏览器烘焙的 Cookie，
子进程拿不到；Atlas 的 `DemoApi` 已与 Playwright 会话共享，直接调用最稳。

报文模板来自 2026-11-01 真实 UI 保存请求捕获，不是猜的。
"""
from __future__ import annotations
import json
from pathlib import Path

AUTO_TAG = "非人工创建"
SAVE_PATH = "/web/demo-sales/saleOrder/saveSalesOrder"


def build_payload(template_path: Path, where: dict) -> dict:
    payload = json.loads(Path(template_path).read_text(encoding="utf-8"))
    order = payload["saleOrder"]
    remark = str(where.get("remark") or "API建单验证")
    if AUTO_TAG not in remark:
        remark = f"{remark}{AUTO_TAG}"
    order["remark"] = remark
    for key, target in (("customer_code", "customerCorpNumber"),
                        ("customer_code_full", "customerCode"),
                        ("payment_terms", "paymentTerms"),
                        ("warehouse_code", "warehouseCode")):
        if where.get(key):
            order[target] = where[key]
    return payload


def extract_order_no(response: dict) -> str:
    data = response.get("data") or {}
    if not isinstance(data, dict):
        return ""
    inner = data.get("data")
    if isinstance(inner, dict):
        for key in ("orderNo", "docNo"):
            if inner.get(key):
                return str(inner[key])
    for key in ("orderNo", "docNo"):
        if data.get(key):
            return str(data[key])
    return ""


def make_sales_order_creator(demo_api, template_path: Path, *, default_remark: str = "API建单验证"):
    """返回可直接交给 DataProbe 的 create 函数。"""
    def create(where: dict) -> dict:
        payload = build_payload(template_path, {**where, "remark": where.get("remark", default_remark)})
        response = demo_api.demo_call("POST", SAVE_PATH, body=payload)
        order_no = extract_order_no(response)
        if not order_no:
            raise RuntimeError(
                f"建单失败，未返回单号：status={response.get('status')} "
                f"body={json.dumps(response.get('data'), ensure_ascii=False)[:400]}")
        return {"orderNo": order_no, "remark": payload["saleOrder"]["remark"],
                "paymentTerms": payload["saleOrder"].get("paymentTerms", ""),
                "customerCorpNumber": payload["saleOrder"].get("customerCorpNumber", "")}
    return create
