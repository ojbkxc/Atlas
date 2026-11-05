"""Gap2 `atlas config add`：微服务配置生成器。

两种流程：
- --mapping（流程一）：只出 ATLAS_* 映射片段（零代码），复用现有插件形状。
- 默认（流程二）：生成 plugins/<domain>/{plugin,tasks,probes}.py + knowledge/*.json
  空骨架 + env 片段。

红线遵守：
- #10 无审核不写知识：knowledge/*.json 全为空（pitfalls:[] / contracts:[]）
- #12 不造形式代码：Task perform 抛 NotImplementedError；allowed_api_operations 空集
- #11 业务在插件，不进引擎：生成器是数据的搬运工，不内联客户名
- 不碰引擎代码：用 resolve_plugin 缝接入（已有）
"""
from __future__ import annotations
import json
import re
from pathlib import Path

from .config import ENV_PREFIX_DEFAULT, ENV_URL_SUFFIXES, env_url_keys


class ConfigAddError(Exception):
    """用户输入/目标冲突 → CLI 走 rc 2（与 cli._user_error 同口径）。"""


_DOMAIN_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def _validate_domain(domain: str) -> str:
    """domain 必须是合法 Python 包名（^[a-z][a-z0-9_]*$）。"""
    if not _DOMAIN_RE.match(domain):
        raise ConfigAddError(
            f"domain {domain!r} 非法：必须是小写字母开头、仅含小写字母/数字/下划线"
            "（^[a-z][a-z0-9_]*$）")
    return domain


def _capitalize(domain: str) -> str:
    return domain[:1].upper() + domain[1:] if domain else domain


def _render(text: str, ctx: dict) -> str:
    for key, value in ctx.items():
        text = text.replace("{{" + key + "}}", str(value))
    return text


def _write(dest: Path, rel: str, content: str) -> str:
    dst = dest / rel
    if dst.exists():
        raise ConfigAddError(f"目标已存在：{dst}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(content, encoding="utf-8")
    return rel


# ---- plugins/<domain>/__init__.py ----
_INIT = '''\
"""{{domain}} 业务插件：结构镜像 DemoPlugin，接入靠 ATLAS_PLUGIN=plugins.{{domain}}
（不改引擎）。引擎不含任何 {{domain}} 业务字面量。

导入时自动 register_plugin，使 resolve_plugin() 能发现本插件。
"""
from .plugin import {{Domain}}Plugin
from atlas.plugin_api import register_plugin

PLUGIN = {{Domain}}Plugin()
register_plugin("{{domain}}", {{Domain}}Plugin)
'''

# ---- plugins/<domain>/plugin.py ----
_PLUGIN = '''\
"""{{domain}} 业务插件：结构镜像 DemoPlugin，接入靠 ATLAS_PLUGIN=plugins.{{domain}}
（不改引擎）。

八个契约成员（与 DemoPlugin 一致，使 resolve_plugin() 返回可被 cli/runtime 无差别消费）：
  name / allowed_api_operations / knowledge / selectors / contracts /
  session_defaults / register_tasks / make_provider
"""
from pathlib import Path
import json
import os
from atlas.contracts import ContractRegistry
from atlas.dsl import TaskRegistry
from atlas.knowledge import KnowledgeBase, SelectorRegistry
from atlas.probes import DataProvider
from .tasks import core_tasks
from .probes import make_provider as _make_provider


class {{Domain}}Plugin:
    name = "{{domain}}"
    # 仅声明真实存在且只读的 API 操作名；空集合 = 不启用 API 白名单（红线 12 不伪造）
    allowed_api_operations = frozenset()

    def __init__(self):
        self.knowledge_root = Path(__file__).parent / "knowledge"
        _priv = os.environ.get("ATLAS_KNOWLEDGE_DIR")
        self.private_root = Path(_priv) if _priv else None

    def knowledge(self) -> KnowledgeBase:
        if self.private_root:
            return KnowledgeBase(self.private_root, fallback_root=self.knowledge_root)
        # 只读库：未设 ATLAS_KNOWLEDGE_DIR 时不产出可写实例（#135 红线 #11）
        return KnowledgeBase(None, fallback_root=self.knowledge_root)

    def selectors(self) -> SelectorRegistry:
        return SelectorRegistry(self.knowledge().load("components"))

    def contracts(self) -> ContractRegistry:
        # 与 DemoPlugin 同款：私有→包内逐文件回退，两级皆缺显式报错（红线 7）
        candidates = []
        if self.private_root:
            candidates.append(self.private_root / "contracts" / "core.json")
        candidates.append(self.knowledge_root / "contracts" / "core.json")
        for path in candidates:
            if path.exists():
                try:
                    return ContractRegistry.load(path)
                except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
                    continue
        raise ValueError(f"契约文件缺失或损坏：{candidates}")

    def session_defaults(self) -> dict:
        from .tasks import overlay_session
        return overlay_session(self.knowledge().load("session") or {})

    def register_tasks(self, registry: TaskRegistry, legacy_pages_path=None) -> None:
        for task in core_tasks(legacy_pages_path):
            registry.register(task.name, lambda task=task, **kwargs: task.clone(**kwargs))

    def make_provider(self, config, api, division_code) -> DataProvider:
        return _make_provider(config, api, division_code)
'''

# ---- plugins/<domain>/tasks.py ----
_TASKS = '''\
"""{{domain}} 最小 Task 骨架。perform 未实现 → NotImplementedError 点名；
禁止伪造「看起来完整」的实现（AGENTS 红线 12）。实现后把 read_only/intent 改准确。
"""
from __future__ import annotations
from atlas.core import Channel, Task, TaskMetadata

SESSION_ENV_KEYS = {"account": "ATLAS_SESSION_ACCOUNT", "ou": "ATLAS_SESSION_OU",
                    "division_code": "ATLAS_SESSION_DIVISION",
                    "language": "ATLAS_SESSION_LANGUAGE"}


def overlay_session(defaults: dict) -> dict:
    import os
    for key, env in SESSION_ENV_KEYS.items():
        value = os.environ.get(env, "").strip()
        if value:
            defaults[key] = value
    return defaults


class {{Domain}}Task(Task):
    function = ""
    defaults: dict = {}

    def __init__(self, metadata: TaskMetadata, defaults: dict | None = None, **kwargs):
        self.metadata = metadata
        self.function = metadata.tags[0]
        self.defaults = {**type(self).defaults, **(defaults or {})}
        self.params = {**self.defaults, **kwargs}

    @property
    def name(self) -> str:
        return self.function

    def clone(self, **kwargs) -> "{{Domain}}Task":
        return type(self)(self.metadata, defaults=self.defaults, **kwargs)

    def perform(self, actor):
        # TODO(人工)：接真实页面库/接口。未实现就抛，绝不返回假成功（红线 7/12）。
        raise NotImplementedError(
            f"{{domain}}.{{function}} 尚未实现：请在 plugins/{{domain}}/tasks.py 接入真实操作或页面库"
            .replace("{function}", self.function))


def _task(function, intent, *, requires=(), produces=(), channel=Channel.UI,
          read_only=False, defaults=None):
    return {{Domain}}Task(TaskMetadata(domain="{{domain}}", intent=intent,
        requires=tuple(requires), produces=tuple(produces), channel=channel,
        read_only=read_only, tags=(function,)), defaults=defaults)


def core_tasks(legacy_pages_path=None) -> list[{{Domain}}Task]:
    # 与 --tasks 参数对应；示例四个，用户按需增删。元数据必须与将来 perform 的读写一致。
    specs = [
        ("open_list", "打开 {{domain}} 列表", (), ("list_ready",), Channel.UI, True, {}),
        ("click_new", "进入新建页", ("list_ready",), ("new_ready",), Channel.UI, False, {}),
        ("save", "保存单据", ("new_ready",), ("save_result",), Channel.UI, False, {}),
        ("wait_result", "等待保存结果取单号", ("save_result",), ("document_no",),
         Channel.UI, True, {}),
    ]
    return [_task(f, i, requires=r, produces=p, channel=c, read_only=ro, defaults=d)
            for (f, i, r, p, c, ro, d) in specs]
'''

# ---- plugins/<domain>/probes.py ----
_PROBES = '''\
"""{{domain}} 数据探针骨架。find 需真实只读实现；create 默认禁用
（红线 3/16：非验证脚本不建单）。"""
from atlas.probes import CallableProbe, DataProvider


class {{Domain}}Probe(CallableProbe):
    def __init__(self, api, division_code, creator=None):
        self.api, self.division_code = api, division_code
        super().__init__("{{Domain}}Entity", self._find, creator)

    def _find(self, where):
        # TODO(人工)：接真实只读查询。没有就返回 None（诚实），不要伪造。
        return None


def make_provider(config, api, division_code) -> DataProvider:
    return DataProvider(config, [{{Domain}}Probe(api, division_code)])
'''

# ---- knowledge/session.json ----
_SESSION_JSON = '{"account":"","ou":"","division_code":"","language":"English US"}'

# ---- knowledge/envs.json ----
def _build_envs_json(prefix: str) -> str:
    envs = {}
    for env_name, suffix in sorted(ENV_URL_SUFFIXES.items()):
        url_key = f"{prefix}{suffix}"
        if env_name.startswith("prod"):
            write_policy = "forbidden"
            sso = False
        elif env_name == "sit":
            write_policy = "allowed_with_guards"
            sso = False
        else:
            write_policy = "ask_first"
            sso = "unverified"
        envs[env_name] = {"url_key": url_key, "write": write_policy, "sso_captcha": sso}
    return json.dumps({"environments": envs}, ensure_ascii=False)

# ---- knowledge/domains.json ----
_DOMAINS_JSON = '{"domains":{}}'

# ---- knowledge/project_map.json ----
_PROJECT_MAP_JSON = json.dumps({
    "project": "", "stack": "", "channels": {}, "suites": [],
    "entrypoints": {}, "invariants": []
}, ensure_ascii=False)

# ---- knowledge/pitfalls.json ----
_PITFALLS_JSON = '{"pitfalls":[]}'

# ---- knowledge/components.json ----
_COMPONENTS_JSON = '{"components":{},"selectors":{}}'

# ---- knowledge/contracts/core.json ----
_CONTRACTS_JSON = json.dumps({
    "policy": "分级而非全量：core 做完整字段校验，high 只查通用规则，minimal 只判存活。",
    "contracts": []
}, ensure_ascii=False, indent=2)

# ---- .env.<domain>.example ----
_ENV_EXAMPLE = """\
# {{domain}} 系统映射（合并进项目 .env 或设为环境变量）
# 激活插件（流程二）；流程一复用现有插件时不需要它
ATLAS_PLUGIN=plugins.{{domain}}
# 凭据/URL 键前缀整体切换（URL 与凭据键一起换）
ATLAS_ENV_PREFIX={{env_prefix}}
# 旧项目资产映射
ATLAS_LEGACY_PROJECT=../<legacy-project>
ATLAS_LEGACY_WEB=<legacy-web>
ATLAS_DEMO_API_PROJECT=../<legacy-api>
ATLAS_DEMO_API_FILE=<api_file>
ATLAS_DEMO_API_CLASS=<ApiClass>
ATLAS_DEMO_API_SPEC=<api_spec>
ATLAS_APP_HEADER_KEY=<app_header_key>
ATLAS_APP_HEADER_VALUE=<app_header_value>
# 会话默认值（真实项目必须覆盖；密码走 {{env_prefix}}PWD_<账号大写>）
ATLAS_SESSION_ACCOUNT=
ATLAS_SESSION_OU=
ATLAS_SESSION_DIVISION=
ATLAS_SESSION_LANGUAGE=English US
# 私有知识目录（真实坑点/组件/坐标写这里，仓库零污染）
ATLAS_KNOWLEDGE_DIR=
"""


def _build_mapping_fragment(domain: str, env_prefix: str) -> dict:
    """流程一（--mapping）：只出 ATLAS_* 映射片段，零代码。"""
    return {
        "domain": domain,
        "env_prefix": env_prefix,
        "env_fragment": {
            "ATLAS_PLUGIN": f"plugins.{domain}",
            "ATLAS_ENV_PREFIX": env_prefix,
            "ATLAS_LEGACY_PROJECT": "../<legacy-project>",
            "ATLAS_LEGACY_WEB": "<legacy-web>",
            "ATLAS_DEMO_API_PROJECT": "../<legacy-api>",
            "ATLAS_DEMO_API_FILE": "<api_file>",
            "ATLAS_DEMO_API_CLASS": "<ApiClass>",
            "ATLAS_DEMO_API_SPEC": "<api_spec>",
            "ATLAS_APP_HEADER_KEY": "<app_header_key>",
            "ATLAS_APP_HEADER_VALUE": "<app_header_value>",
            "ATLAS_SESSION_ACCOUNT": "",
            "ATLAS_SESSION_OU": "",
            "ATLAS_SESSION_DIVISION": "",
            "ATLAS_SESSION_LANGUAGE": "English US",
            "ATLAS_KNOWLEDGE_DIR": "",
        },
        "next": [
            f"把 ATLAS_PLUGIN=plugins.{domain} 写进 .env，或设环境变量",
            "atlas doctor",
            "atlas tasks",
        ],
    }


def add_config(domain, *, mapping_only=False, out_dir=Path(".")) -> dict:
    """生成微服务配置骨架。

    参数：
      domain       业务域标识（^[a-z][a-z0-9_]*$）
      mapping_only 流程一：只出映射片段，不生成插件目录
      out_dir      项目根（默认当前目录）

    返回 dict：{"domain", "plugins_dir", "files", "activate", "env_fragment", "next"}

    冲突防护：plugins/<domain> 已存在默认拒绝（ConfigAddError → CLI rc 2）。
    """
    _validate_domain(domain)
    root = Path(out_dir).resolve()
    env_prefix = ENV_PREFIX_DEFAULT
    Domain = _capitalize(domain)

    # 流程一：只出映射片段，零代码
    if mapping_only:
        return _build_mapping_fragment(domain, env_prefix)

    # 流程二：生成插件骨架
    plugins_dir = root / "plugins" / domain
    if plugins_dir.exists():
        raise ConfigAddError(f"plugins/{domain} 已存在；请先清空或换 domain")

    ctx = {"domain": domain, "Domain": Domain, "env_prefix": env_prefix}
    plugins_dir.mkdir(parents=True, exist_ok=True)
    files = []

    # __init__.py
    files.append(_write(root, f"plugins/{domain}/__init__.py", _render(_INIT, ctx)))

    # plugin.py
    files.append(_write(root, f"plugins/{domain}/plugin.py", _render(_PLUGIN, ctx)))

    # tasks.py
    files.append(_write(root, f"plugins/{domain}/tasks.py", _render(_TASKS, ctx)))

    # probes.py
    files.append(_write(root, f"plugins/{domain}/probes.py", _render(_PROBES, ctx)))

    # knowledge/session.json
    files.append(_write(root, f"plugins/{domain}/knowledge/session.json", _SESSION_JSON))

    # knowledge/envs.json
    files.append(_write(root, f"plugins/{domain}/knowledge/envs.json",
                        _build_envs_json(env_prefix)))

    # knowledge/domains.json
    files.append(_write(root, f"plugins/{domain}/knowledge/domains.json", _DOMAINS_JSON))

    # knowledge/project_map.json
    files.append(_write(root, f"plugins/{domain}/knowledge/project_map.json",
                        _PROJECT_MAP_JSON))

    # knowledge/pitfalls.json（空——红线 10：不预置知识）
    files.append(_write(root, f"plugins/{domain}/knowledge/pitfalls.json", _PITFALLS_JSON))

    # knowledge/components.json
    files.append(_write(root, f"plugins/{domain}/knowledge/components.json",
                        _COMPONENTS_JSON))

    # knowledge/contracts/core.json（空——红线 12：不伪造强契约）
    files.append(_write(root, f"plugins/{domain}/knowledge/contracts/core.json",
                        _CONTRACTS_JSON))

    # .env.<domain>.example
    files.append(_write(root, f".env.{domain}.example", _render(_ENV_EXAMPLE, ctx)))

    env_fragment = _build_mapping_fragment(domain, env_prefix)["env_fragment"]

    return {
        "domain": domain,
        "plugins_dir": str(plugins_dir),
        "files": files,
        "activate": f"ATLAS_PLUGIN=plugins.{domain}",
        "env_fragment": env_fragment,
        "next": [
            f"把 ATLAS_PLUGIN=plugins.{domain} 写进 .env，或设环境变量",
            "PYTHONPATH 含项目根后 atlas doctor",
            "atlas tasks  # 列骨架 Task",
            "在 plugins/{domain}/tasks.py 实现 perform（当前 NotImplementedError）",
        ],
    }
