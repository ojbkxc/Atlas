"""探针生成：只实现 find() 的 DataProbe skeleton。

红线 3/12/16：不造形式代码、不自动创建无备注单据、建单报文须来自真实捕获。
create() 一律 NotImplementedError；不生成 creator。
"""
from __future__ import annotations

from .dsl_utils import pascal_identifier, slugify
from .model import Endpoint


PROBE_HEADER = '''# 生成候选：只实现 find()（零副作用查询）；create() 一律 NotImplementedError。
# 不要在此伪造建单——建单能力必须来自真实捕获的已验证脚本（红线 3/12/16）。
from atlas.probes import DataProbe
'''

# 探针身份兜底名（类名 + entity 名同一串；`probe_entity_name` 唯一取值口）
PROBE_FALLBACK_NAME = "GeneratedProbe"
# 有效 slug 的最短长度：中文文档经 slugify 会只剩 1~2 个残渣字符（如 `订单 \s 查询` → `s`），
# 那种残渣当类名是「看着有个名字、实际不知道是什么」——按没有名字处理，退到文件名/兜底。
_MIN_SLUG = 3


def probe_entity_name(summary: str | None, stem: str | None = None) -> str:
    """探针类名与 ``entity`` 名的**唯一取值口**（必须同时是合法 Python 标识符）。

    取值链：``summary`` → ``stem``（源文件名）→ ``PROBE_FALLBACK_NAME``，取第一个够用的
    ASCII slug 再转 Pascal 加 ``Probe`` 后缀。为什么要有这条链（三条真实缺陷，全都不报错）：
    ① 旧实现把 ``slugify(..., fallback="GeneratedProbe")`` 的结果再拼 ``Probe``，中文标题
    （本项目主线文档语言）恒得 ``GeneratedProbeProbe``——兜底词本身已经带后缀；
    ② ``slugify`` 把非 ASCII 整段抹掉，``订单 \\s 查询`` 只剩 ``s``，成品是
    ``class sProbe(DataProbe)``：既不是业务名也不是兜底名；
    ③ 更要紧的一条：``slugify`` 保留 ``-``，英文摘要出的 ``get-web-example-orderProbe``
    **不是合法标识符**，`atlas gen` 照样写出 ``class get-web-example-orderProbe(DataProbe):``
    —— 产物是个语法错的 .py，而生成期只校验本仓源码（#9 同一条缝）。故这里走
    ``pascal_identifier``（与页对象类名同一把刀，见 dsl_utils）。
    """
    for candidate in (summary, stem):
        slug = slugify(candidate or "", fallback="").strip("-_")
        if len(slug) >= _MIN_SLUG:
            ident = pascal_identifier(slug, fallback="")
            if len(ident) >= _MIN_SLUG:
                return f"{ident}Probe"
    return PROBE_FALLBACK_NAME


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
