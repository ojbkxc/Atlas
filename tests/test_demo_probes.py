from atlas.plugins.demo.probes import CustomerProbe, SalesOrderProbe, matches


class FakeApi:
    """按页返回数据，用于验证探针不会只看第一页。"""
    def __init__(self, pages, payments=None):
        self.pages, self.payments, self.calls = pages, payments or [], []

    def sale_order_list(self, division_code, page=1, page_size=50, **kw):
        self.calls.append((page, page_size))
        rows = self.pages[page - 1] if page <= len(self.pages) else []
        return {"ok": True, "data": {"resultCode": "BUG-000", "data": rows}}

    def customer_list(self, division_code, **kw):
        return {"ok": True, "data": {"data": [{"customerCode": "E02179010243",
                                               "customerCorpNumber": "E0217901"}]}}

    def customer_detail(self, code, division_code, **kw):
        return {"ok": True, "data": {"data": [], "collectPayments": self.payments}}


def test_probe_scans_beyond_first_page():
    api = FakeApi([[{"orderNo": "SP1", "orderStatus": "1001"}],
                   [{"orderNo": "SP2", "orderStatus": "1003", "paymentTerms": "90DI"}]])
    probe = SalesOrderProbe(api, "DIV-DEMO-0001")
    probe.default_page_size = 1
    hit = probe.find({"status": [1003, 1006]})
    assert hit["orderNo"] == "SP2"
    assert [page for page, _ in api.calls] == [1, 2]


def test_probe_stops_on_short_page():
    api = FakeApi([[{"orderNo": "SP1", "orderStatus": "1001"}]])
    probe = SalesOrderProbe(api, "DIV-DEMO-0001")
    probe.default_page_size = 5
    assert probe.find({"status": [1003]}) is None
    assert len(api.calls) == 1


def test_customer_probe_matches_pt_count_expression():
    api = FakeApi([], payments=[{"a": 1}, {"b": 2}, {"c": 3}])
    hit = CustomerProbe(api, "DIV-DEMO-0001").find({"customer_code": "E0217901", "pt_count": ">1"})
    assert hit["customerCorpNumber"] == "E0217901"


def test_pt_count_is_not_matched_against_list_rows():
    assert matches({"customerCorpNumber": "E1"}, {"pt_count": ">1"}) is True


def test_make_demo_provider_registers_both_probes(tmp_path):
    """make_demo_provider 注册 SalesOrder 和 Customer 两个探针。"""
    from atlas.config import AtlasConfig
    from atlas.plugins.demo import make_demo_provider
    config = AtlasConfig(tmp_path)
    provider = make_demo_provider(config, object(), "DIV-DEMO-0001")
    assert "SalesOrder" in provider.probes
    assert "Customer" in provider.probes
