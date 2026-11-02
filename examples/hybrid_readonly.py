"""真实只读混用：UI 建立会话，API 查询订单并输出最小结果，不打印 Cookie/响应隐私。"""
import json
from atlas.config import AtlasConfig
from atlas.runtime import DemoRuntime

config = AtlasConfig.from_root(env="sit", mode="run")
with DemoRuntime(config) as runtime:
    actor = runtime.actor
    result = runtime.demo_api.sale_order_list(runtime.division_code, page=1, page_size=1)
    if not result.get("ok"):
        raise RuntimeError(f"销售单只读查询失败：HTTP {result.get('status')}")
    data = result.get("data", {})
    print(json.dumps({"ui_session": not actor.page.is_closed(), "api_ok": True,
                      "response_type": type(data).__name__}, ensure_ascii=False))
