"""探针生成：requires 声明 + 只实现 find() 的 DataProbe skeleton。

红线 3/12/16：不造形式代码、不自动创建无备注单据、建单报文须来自真实捕获。
create() 一律 NotImplementedError；不生成 creator。
"""
from __future__ import annotations
from typing import Any

from .model import Endpoint
from .emit_flow import _build_requires


PROBE_HEADER = '''# 生成候选：只实现 find()（零副作用查询）；create() 一律 NotImplementedError。
# 不要在此伪造建单——建单能力必须来自真实捕获的已验证脚本（红线 3/12/16）。
from atlas.probes import DataProbe
'''


def emit_probe_skeleton(endpoints: list[Endpoint], *, scenario: str = "auto",
                        entity_name: str = "GeneratedProbe") -> str:
    """生成 DataProbe skeleton 源码文本（只 find）。"""
    lines = [PROBE_HEADER,
             f'class {entity_name}(DataProbe):',
             f'    entity = "{entity_name}"',
             '',
             '    def __init__(self, demo_api):',
             '        self.api = demo_api',
             '',
             '    def find(self, where):',
             '        # TODO(人审)：确认该查询已在 DemoPlugin.allowed_api_operations 白名单',
             '        # resp = self.api.<已验证只读查询>(...)',
             '        # 沿文档给出的字段路径筛选 where；查不到返回 None',
             '        raise NotImplementedError("生成候选：请接已审核的只读查询实现")',
             '',
             '    def create(self, where):',
             '        raise NotImplementedError("生成候选不提供建单；请接已验证 creator（ScriptedCreator）")',
             '']
    return "\n".join(lines)


def emit_requires(endpoints: list[Endpoint], scenario: str = "auto") -> dict:
    """requires 块（与 emit_flow 共用 _build_requires，单一真相）。"""
    return _build_requires(endpoints, scenario)
