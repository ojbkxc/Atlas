"""iService 数据探针骨架：只读 find 未接真实查询（返回 None 诚实），create 恒禁用。

红线 #3/#16：非经过验证的脚本不建单。Atlas demo 的 creator 来自真实 UI 保存请求捕获，
iService 侧暂无同等证据，故不伪造 creator——find_or_create 对本插件只会停在「找不到」，
不会造出「看起来完整」的假单据（与 AGENTS 红线 #12 一致）。
"""
from ...probes import CallableProbe, DataProvider


class IserviceProbe(CallableProbe):
    def __init__(self, api, division_code, creator=None):
        self.api, self.division_code = api, division_code
        # creator 缺省 None：没有受审核的建单脚本时不提供建单能力。
        super().__init__("IserviceEntity", self._find, creator)

    def _find(self, where):
        # 未接真实只读查询。诚实返回 None（缺数据），不伪造数据、不猜。
        return None


def make_provider(config, api, division_code) -> DataProvider:
    return DataProvider(config, [IserviceProbe(api, division_code)])
