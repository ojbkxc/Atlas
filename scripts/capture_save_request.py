"""一次性探查：捕获真实 UI 保存时发出的建单请求（URL/headers/body 结构）。

用途：为真实 API 建单实现取到**服务端实际接受**的报文，而不是凭猜测拼字段。
产出写到 artifacts/capture/ 下，不含 Cookie 值。
"""
from __future__ import annotations
import json

from atlas.config import AtlasConfig
from atlas.runtime import DemoRuntime

SAVE_HINTS = ("saleOrder", "saveOrder", "insertAll", "createOrder")


def main() -> int:
    config = AtlasConfig.from_root(env="sit", mode="run")
    out = config.artifacts_dir / "capture"
    out.mkdir(parents=True, exist_ok=True)
    captured: list[dict] = []

    with DemoRuntime(config) as runtime:
        page = runtime.actor.page

        def on_request(req):
            url = req.url
            if req.method != "POST" or not any(h in url for h in SAVE_HINTS):
                return
            try:
                body = req.post_data
            except Exception:
                body = None
            captured.append({
                "url": url,
                "path": "/" + url.split("/", 3)[-1] if "://" in url else url,
                "headers": {k: v for k, v in req.headers.items()
                            if k.lower() not in {"cookie", "authorization"}},
                "body": body,
            })

        page.on("request", on_request)

        from atlas.dsl import FlowCompiler
        flow = {
            "id": "capture-save-request",
            "steps": [
                {"task": "open_doc_list", "page_key": "sales_order", "channel": "ui"},
                {"task": "click_new", "page_key": "sales_order"},
                {"task": "select_customer", "code": "CUST0000001", "code_full": "CUST00000010001"},
                {"task": "read_pt"},
                {"task": "ensure_warehouse", "wh": "WH-DEMO"},
                {"task": "ensure_sales_person"},
                {"task": "mount_other_header"},
                {"task": "click_save"},
                {"task": "wait_save_result"},
            ],
        }
        FlowCompiler(runtime.registry(), runtime.provider).run(flow, runtime.actor)

    path = out / "save_requests.json"
    path.write_text(json.dumps(captured, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"captured {len(captured)} requests -> {path}")
    for item in captured:
        print(" ", item["path"][:110], "body_len=", len(item["body"] or ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
