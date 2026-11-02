"""Demo 探针：读接口优先；未审核的建单器绝不由框架猜测。"""
from __future__ import annotations
from typing import Callable
from ...probes import CallableProbe, DataProvider
from ...config import AtlasConfig

def _rows(response: dict) -> list[dict]:
    data = response.get("data", {})
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("data", "records", "list", "rows"):
            if isinstance(data.get(key), list):
                return data[key]
    return []

def matches(row: dict, where: dict) -> bool:
    aliases = {"status": "orderStatus", "customer_code": "customerCorpNumber"}
    for key, expected in where.items():
        if key == "pt_count":
            continue  # 由 CustomerProbe 查客户详情比对，列表行没有该字段。
        if "." in key:
            continue  # 嵌套客户 PT 条件由 CustomerProbe 另行判断，不可假匹配。
        actual = row.get(aliases.get(key, key))
        if isinstance(expected, list):
            if actual not in expected and str(actual) not in {str(v) for v in expected}:
                return False
        elif isinstance(expected, str) and expected.startswith(">"):
            if actual is None or not float(actual) > float(expected[1:]):
                return False
        elif actual != expected and str(actual) != str(expected):
            return False
    return True

class SalesOrderProbe(CallableProbe):
    default_page_size = 200
    max_pages = 10

    def __init__(self, demo_api, division_code: str, creator: Callable | None = None):
        self.demo_api, self.division_code = demo_api, division_code
        super().__init__("SalesOrder", self._find, creator)

    def _find(self, where: dict) -> dict | None:
        # 列表接口默认只返回第一页；草稿占绝大多数，只查第一页会误判“无来源单”。
        query = {key: value for key, value in where.items() if key in {"customer_code"}}
        if "customer_code" in query:
            query["customerCorpNumber"] = query.pop("customer_code")
        for page in range(1, self.max_pages + 1):
            response = self.demo_api.sale_order_list(
                self.division_code, page=page, page_size=self.default_page_size, **query)
            if not response.get("ok"):
                return None
            rows = _rows(response)
            hit = next((row for row in rows if matches(row, where)), None)
            if hit is not None:
                return hit
            if len(rows) < self.default_page_size:
                return None
        return None

class CustomerProbe(CallableProbe):
    def __init__(self, demo_api, division_code: str, creator: Callable | None = None):
        self.demo_api, self.division_code = demo_api, division_code
        super().__init__("Customer", self._find, creator)

    def _find(self, where: dict) -> dict | None:
        response = self.demo_api.customer_list(self.division_code)
        if not response.get("ok"):
            return None
        for row in _rows(response):
            if not matches(row, {k: v for k, v in where.items() if k != "pt_count"}):
                continue
            if "pt_count" in where:
                code = row.get("customerCode") or row.get("customerCorpNumber")
                detail = self.demo_api.customer_detail(code, self.division_code)
                payments = _rows(detail)
                if not payments and isinstance(detail.get("data"), dict):
                    payments = detail["data"].get("collectPayments", [])
                # pt_count 形如 ">1"；复用条件解析，不要硬编码相等比较。
                if not matches({"pt_count": len(payments)}, {"pt_count": where["pt_count"]}):
                    continue
            return row
        return None

def make_demo_provider(config: AtlasConfig, demo_api, division_code: str,
                      sales_order_creator: Callable | None = None,
                      customer_creator: Callable | None = None) -> DataProvider:
    return DataProvider(config, [SalesOrderProbe(demo_api, division_code, sales_order_creator),
                                 CustomerProbe(demo_api, division_code, customer_creator)])
