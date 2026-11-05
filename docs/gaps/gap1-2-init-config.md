# Gap 1 & 2 设计：`atlas init`（项目初始化向导）+ `atlas config add`（微服务配置生成器）

> 状态：**已落地**（src/atlas/scaffold.py + config_add.py）。作者：gap-init。日期：2026-11-04。
> 约束来源：`AGENTS.md`（红线，冲突以它为准）、`CLAUDE.md`、`README.md`。
> 报告格式约束（team-lead）：项目测试报告统一 **Allure 2.39.0**；初始化向导生成的骨架必须能
> 跑完产出 Allure 结果。**实现方式按 gap5 方案 A**：骨架不装 `allure-pytest`，由 `atlas allure`
> （纯 stdlib）从 `reports/runs.jsonl` 直出 Allure 2.39.0 结果集（见 §2.7/§2.8，权威口径见 `gap5-allure.md`）。

---

## 0. 现状调研结论（建立心智模型）

### 0.1 Atlas 如何对接真实项目（关键机制）

Atlas 是「通用引擎 + 虚构 demo + `ATLAS_*` 映射」三层结构。引擎里**零业务字面量**（红线 11），
真实项目通过环境变量映射覆盖默认值：

| 机制 | 位置 | 作用 |
|---|---|---|
| `.atlas-root` 标记文件 | 仓库根 | `AtlasConfig.from_root()` **向上逐级查找**该文件定位项目根（`config.py:86`）。没有它，`.env` 加载与 root 解析都会落到 `cwd`。 |
| `.env` + `ATLAS_ENV_PREFIX` | `config.py:load_dotenv` | 凭据/URL 键前缀默认 `DEMO_`；真实项目整体切 `ISERVICE_`（URL 与凭据一起换）。BOM/UTF-16 兜底解析。 |
| `ATLAS_LEGACY_*` | `config.py` | 旧 UI 项目根 / web 子目录 / 默认 pytest 节点。 |
| `ATLAS_DEMO_API_*` | `config.py` | 旧 API 客户端模块文件 / 类名 / 接口目录文件（`legacy-api/scripts/<file>`）。 |
| `ATLAS_APP_HEADER_*` | `config.py` | 后端校验的业务识别头（缺它后端回 `BUG-920`）。 |
| `ATLAS_KNOWLEDGE_DIR` | `plugin.py` | 私有知识目录：**读逐文件回退包内虚构默认，写只落私有**，仓库零污染。 |
| `ATLAS_SESSION_*` | `tasks.py:overlay_session` | 会话默认值（账号名/OU/division/语言）覆盖，密码永不在此。 |
| `ATLAS_BROWSER_CHANNEL` / `ATLAS_BROWSER_EXECUTABLE` | `session.py:34` | 浏览器通道（默认 `chrome`）/ 系统浏览器可执行文件（Linux CI 用）。 |

**结论**：一个「新 Atlas 项目」的最小可跑形态 = 一个含 `.atlas-root` + `.env` 的目录，
加上已安装的 `atlas-test-platform` 库。**不需要复制 Atlas 源码**。

### 0.2 `atlas doctor` 校验什么（`cli.py:329-363`）

输出 JSON：`python` / `root` / `env` / `env_prefix` / `base_url_configured` / `base_url_source` /
`legacy_web` / `legacy_pages`(legacy/lib/pages.py 存在) / `legacy_pytest_ini` /
`demo_api`(api/scripts/<file> 存在) / `knowledge_pitfalls`(条数)。
**返回码**：`legacy_pages and demo_api and docker_ok` 全真才 rc 0，否则 rc 1。
关键坑（已修）：doctor 与 `DemoRuntime` 一样先 `load_dotenv(legacy)` 继承旧项目 `.env`，否则
误报「未配置」；`_read_text_safe` 对不可读 Docker 资产记 `docker_unreadable` 而非崩掉诊断。

**对 generator 的含义**：新项目刚生成时必然 `legacy_pages=False` / `demo_api=False`（没有旧项目），
所以 `atlas doctor` 会 rc 1 —— 这是**正常且诚实**的，generator 的 README 必须解释这一点，
不得为了 rc 0 去伪造 `legacy-suite` 目录（红线 12）。

### 0.3 `DemoRuntime` 装配链（`runtime.py:60-125`）

```
load_dotenv(legacy)                       # 继承旧项目 .env
→ 校验 base_url / validate_base_url
→ check_demo_api(config)                  # fail-fast：浏览器启动前拒绝坏 API 路径
→ BrowserSession.__enter__()              # 开 Chromium（channel=config.channel）
→ actor = session.actor(account)
→ login (legacy/lib/login.py: do_login → switch_to_ou → switch_to_lang)  # login=True 时
→ actor.bake_session()                    # 浏览器 Cookie 烘焙给 API
→ actor.api.attach_demo(api_project, ...) # 加载外部 API 客户端类
→ demo_api.atlas_allowed_operations = DemoPlugin.allowed_api_operations  # #121 单一真相
→ make_demo_provider(config, demo_api, division_code)  # 探针
→ actor.knowledge / contracts / healer / journal / evidence
→ set_keepalive(...)                      # auth_expired 自动重烘焙
失败：入口任一环节抛错 → 关闭半开 session → 原样抛真因（#111 不留孤儿进程）
退出：回收清单落盘 + session 关闭，双错聚合 ExceptionGroup（#121）
```

`DemoRuntime.registry()` 与 `cli._full_registry()` **同源**（`DemoPlugin.register_tasks` + 5 个通用
Task：`api_call`/`ui_action`/`demo_api`/`assert_api`/`legacy_step`）。`atlas compile` 用
`_full_registry()` 离线校验，保证「compile 绿 = run 不登录也不结构炸」。

### 0.4 `DemoPlugin` 结构（`plugins/demo/plugin.py`）—— 新插件的模板

```python
class DemoPlugin:
    name = "demo"
    allowed_api_operations = frozenset({...})     # 业务白名单（红线 11：放插件不放引擎）
    def __init__(self): knowledge_root=…/knowledge; private_root=ATLAS_KNOWLEDGE_DIR
    def knowledge()  -> KnowledgeBase            # 私有→包内逐文件回退
    def selectors()  -> SelectorRegistry
    def contracts()  -> ContractRegistry         # 私有→包内逐文件回退，两级皆缺显式报错
    def session_defaults() -> dict               # 账号/OU/division/语言（无密码）
    def register_tasks(registry, legacy_pages_path=None) -> None
```

**关键观察**：`cli.py`、`runtime.py` **处处硬编码 `DemoPlugin()`**（`cli.py:223`、`runtime.py:46/100/103/129/141`）。
即：当前**没有插件发现/加载机制**。README「Q: 新增第二个公司的业务怎么做？」给的是
「新建 `plugins/{domain}`」，隐含插件必须放进 `atlas` 包内 —— 对「Atlas 作为已安装库」的
外部项目不可行（会改 site-packages）。这是 Gap 2 必须解决的**唯一引擎级缺口**（见 §3.0）。

### 0.5 知识文件结构（generator 要生成的 schema）

| 文件 | 顶层键 | 用途 |
|---|---|---|
| `session.json` | `account/ou/division_code/language` | 会话默认值（唯一数据源，无密码） |
| `envs.json` | `environments.{sit,uat-*,prod-*}` | 每环境 `url_key/write/sso_captcha` |
| `domains.json` | `domains.{...}` | 业务域路由/常量 |
| `project_map.json` | `project/stack/channels/suites/entrypoints/invariants` | 项目地图 |
| `pitfalls.json` | `pitfalls:[{scope,trigger,...}]` | 坑点（新库可为空数组，`trigger` 非空才参与匹配） |
| `components.json` | `components/selectors` | 选择器注册表 |
| `contracts/core.json` | `policy/contracts:[{name,method,path,level,required?}]` | 契约分级 3 档 |

`KnowledgeBase.load(name)` 对**顶层非映射/坏字节**回落下一来源（私有→包内），两级都坏回落 `{}`。

### 0.6 报告现状（Allure 缺口）

引擎当前报告是 **runlog JSONL**（`reporting.py`：`summarize`/`export_runlog`）+ `reports/*.jsonl`。
`pyproject.toml` 依赖：`playwright/requests/PyYAML`，dev：`pytest/pytest-cov`。
**Allure 目前完全未接入**。Gap 1 要求生成的骨架含 Allure 2.39.0，属新增。

### 0.7 红线速查（设计必须遵守）

引擎不含业务字面量(#11)；禁硬编码凭据/真实 `.env` 入库(#1)；禁生产写(#2)；建单带「非人工创建」
(#3/#16)；禁静默改 legacy(#4)；禁为变绿放宽断言/吞异常/把失败改 SKIP(#7)；禁无审核知识写回(#10)；
禁造形式代码/未验证 API(#12)；禁绕 agent 风险阈值(#13)；Ask-first：引入大型框架依赖/写外部报告(§4)。

---

## 1. 总体设计

新增两件**通用脚手架能力**（生成器，不是业务逻辑）与**一条通用插件加载缝**：

```
新引擎模块（通用、无业务字面量）：
  src/atlas/plugin_api.py     ← 通用插件加载缝（Gap 2 前置，§3.0）
  src/atlas/scaffold.py       ← 两个生成器实现（init_project / add_config）
  # 模板内联在 scaffold.py（设计曾拟 templates/ 目录，实际简化为内联常量）
新 CLI 子命令：
  atlas init [DIR] ...        ← Gap 1
  atlas config add <domain>   ← Gap 2
```

设计原则：
1. **生成器是数据的搬运工**：模板内联在 scaffold.py（设计曾拟 `templates/` package-data，实际简化为内联常量），生成器只做变量替换/落盘，
   自身不写死任何客户名（红线 11）。
2. **复用 `AtlasConfig`**：生成的 `.env`/`.env.example` 严格复用 `config.py` 的键名与 `ATLAS_*` 映射，
   不新造平行配置体系。
3. **不覆盖既有工作**：目标已存在 `.atlas-root` 或非空目录时默认拒绝，需 `--force`。
4. **非交互安全**：默认命令式；`--interactive` 才读 stdin（CI 绝不阻塞）。
5. **诚实**：生成物自述「未实现」的 Task 用 `NotImplementedError` 点名，绝不伪装可跑（红线 12）。

---

## 2. Gap 1：`atlas init`（项目初始化向导）

对应手册流程：**解压骨架模板 → 建虚拟环境 → 装依赖 → 系统初始化 → 验证环境**。

### 2.1 CLI 设计

```
atlas init <name>
    --system SYSTEM           业务系统名（如 ics；必填，描述性标签）
    --module MODULE           业务模块名（如 so；必填，描述性标签）
    --atlas-path PATH         可选：把 Atlas 仓库以相对/绝对 file:// 依赖钉进生成项目
    --out DIR                 输出目录（默认当前目录）
    --interactive             对话式补问缺省值（CI 不设即零交互）

设计稿曾拟但未实现（有意简化）：--domain / --env-prefix / --allure / --allure-cli-version / --no-venv-hint / --force。
冲突防护：目标已存在 .atlas-root 或非空目录默认拒绝（无 --force 覆盖）。
```

**输出**（stdout JSON，便于脚本消费）：
```json
{"created": "<abs dir>", "files": ["pyproject.toml", ".env", ...],
 "next": ["python -m venv .venv && .venv/Scripts/python -m pip install -e .",
          "python -m playwright install chromium",
          "atlas doctor", "pytest -q",
          "atlas allure reports/runs.jsonl --out reports/allure-results --clean",
          "allure serve reports/allure-results"]}
```
返回码：成功 0；目标冲突/参数非法 → 一行 stderr + rc 2（复用 `cli._user_error` 口径，与 #79/#80 一致）。

### 2.2 生成的文件清单

```
<DIR>/
├── .atlas-root                 # "atlas-root = \".\"\n"（复制 Atlas 的标记文件格式）
├── .env                        # 由 .env.example 复制；值全空（无凭据）
├── .env.example                # 由 Atlas .env.example 派生（键前缀用默认 DEMO_；--env-prefix 未实现）
├── .gitignore                  # Atlas .gitignore + reports/allure-result*/ 等
├── pyproject.toml              # 见 §2.3
├── README.md                   # 见 §2.6
├── flows/
│   └── smoke.yaml              # 最小 DSL flow（$task: api_call，见 §2.4）
├── tests/
│   ├── __init__.py             # 空
│   └── test_smoke.py           # pytest + allure，自包含无凭据（见 §2.5）
└── scripts/
    └── run_allure.py           # allure 运行/生成/打开包装（见 §2.7）
```

> 注：不生成 `plugins/`（默认复用 `DemoPlugin`）。加系统/微服务走 `atlas config add`（Gap 2）。

### 2.3 `pyproject.toml`（生成物）

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "{{name}}"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
  # Atlas 作为「已安装库」复用，不复制源码。先装上：pip install -e "<Atlas 仓库>"
  # 或用 atlas init --atlas-path 把下面这行钉成 file:// 依赖。
  "atlas-test-platform",
  "playwright>=1.45",
  "requests>=2.31",
  "PyYAML>=6.0",
  "pytest>=8",
  # 注意：不装 allure-pytest。报告统一走 gap5 方案 A —— `atlas allure`（纯 stdlib）
  # 从 reports/runs.jsonl 直出 Allure 2.39.0 结果集。见 §2.7/§2.8。
]

[tool.pytest.ini_options]
# 不设 --alluredir：结果由 `atlas allure` 产出，不由 pytest 插件写。
testpaths = ["tests"]

[tool.setuptools.packages.find]
where = ["."]
include = ["flows*", "scripts*"]
```

`--atlas-path PATH` 时，`atlas-test-platform` 行替换为：
```toml
  "atlas-test-platform @ file:///{{abs_atlas_path}}",
```
（Windows 用 `file:///D:/GitHub/Atlas`；生成器用 `pathlib.Path(...).as_uri()` 保证跨平台。）

### 2.4 `flows/smoke.yaml`（最小 flow）

用**通用** `api_call`（`CallEndpoint`，`tasks.py:5`）而非任何 demo 业务 Task，保证不绑业务字面量、
且 `atlas compile` 离线可过：

```yaml
# 最小冒烟 flow：仅结构示例。运行需在 .env 配好 base_url（DEMO_URL_SIT）与已登录会话。
# 离线校验：atlas compile flows/smoke.yaml
id: smoke
steps:
  - task: api_call
    path: /health
    method: GET
    save_as: health
assert:
  - health is not null
```
- `atlas compile flows/smoke.yaml` → 结构校验：步骤映射、`api_call` 已注册、assert 块合法（`dsl.validate_flow`）。
- **不**声称可真实运行（缺真实 env 会 `EnvBlocked`/`ValueError`，属数据事实，不伪绿——红线 7/12）。

### 2.5 `tests/test_smoke.py`（纯 pytest，**不装 allure-pytest**）

方案 A 下，pytest 只跑纯 assert 的 smoke；Allure 结果由 `atlas allure` 从 `runs.jsonl` 产出，
所以第二个 smoke 用例负责**产出一份 `reports/runs.jsonl`**，让 `atlas allure` 有输入（全离线、无凭据）。

```python
"""骨架冒烟：验证 Playwright 底座 + 产出 runs.jsonl（供 `atlas allure` 生成 Allure 结果）。
不访问真实业务系统、不需凭据。Allure 结果由 `atlas allure` 直出（gap5 方案 A），
本文件不依赖 allure-pytest。"""
from pathlib import Path

from atlas.config import AtlasConfig
from atlas.core import FunctionalTask, TaskMetadata
from atlas.dsl import FlowCompiler, TaskRegistry
from atlas.session import BrowserSession


def test_browser_baseline():
    """Playwright 执行底座（自包含公开页面，模型同 examples/smoke_browser.py）。"""
    config = AtlasConfig.from_root(env="sit", mode="run")
    with BrowserSession(config) as session:
        session.page.set_content("<title>Atlas smoke</title><button id='ok'>OK</button>")
        session.page.locator("#ok").click()
        assert session.page.title() == "Atlas smoke"


def _local_page_task():
    def _run(actor):
        actor.page.set_content("<title>Atlas engine smoke</title>")
        return {"ok": True}
    return FunctionalTask(
        TaskMetadata(domain="smoke", intent="本地页面执行（产出 runs.jsonl）", produces=("ok",)),
        _run, "local_page_smoke")


def test_engine_emits_runs_jsonl():
    """跑一个最小 Atlas flow（引擎在本地页面执行）→ 写 reports/runs.jsonl，
    供 `atlas allure reports/runs.jsonl --out reports/allure-results` 直出 Allure 结果。"""
    config = AtlasConfig.from_root(env="sit", mode="run")
    registry = TaskRegistry()
    registry.register("local_page_smoke", lambda **_: _local_page_task())
    with BrowserSession(config) as session:
        actor = session.actor("smoke")
        flow = {"id": "smoke", "engine": config, "steps": [{"task": "local_page_smoke"}]}
        result = FlowCompiler(registry).run(flow, actor)
    assert result["status"] == "passed"
    assert (config.reports_dir / "runs.jsonl").is_file()


def test_smoke_flow_compiles():
    """flows/smoke.yaml 离线结构校验（不登录、不执行），与 `atlas compile` 同源。"""
    from atlas.cli import _full_registry
    flow = FlowCompiler(TaskRegistry()).load(Path("flows/smoke.yaml"))
    FlowCompiler(_full_registry()).validate_flow(flow)
```

- `pytest -q` → 全绿并写出 `reports/runs.jsonl`。
- 之后 `atlas allure reports/runs.jsonl --out reports/allure-results --clean` → Allure 2.39.0 结果集。
- 若将来要用官方适配器：属 gap5 **P4 可选** `--via-pytest`，那时才加 `allure-pytest`（钉真实版本），
  见 §2.8。默认骨架**不引入**。

### 2.6 `README.md`（生成物大纲）

1. 一页快速开始：`python -m venv .venv` → `pip install -e .`（含 `--atlas-path` 说明）→
   `python -m playwright install chromium` → `cp .env.example .env` 填真实值 → `atlas doctor`。
2. **诚实说明**：新项目未接旧项目时 `atlas doctor` 会 rc 1（`legacy_pages`/`demo_api` 为 False），
   属预期；接了 `ATLAS_LEGACY_*` / `ATLAS_DEMO_API_*` 后转绿。
3. `ATLAS_*` 映射表（引用 `config.py`，不新造键）+ `ATLAS_SESSION_*` 说明。
4. Allure 用法（§2.7 命令）+ 版本（§2.8）。
5. 加系统/微服务：`atlas config add <domain>`（指向 Gap 2）。

### 2.7 Allure 2.39.0 运行配置（方案 A：`atlas allure` 直出）

生成物**不依赖 allure-pytest**。流程：`pytest`（纯 assert，写 `reports/runs.jsonl`）→
`atlas allure`（纯 stdlib，`runs.jsonl` → Allure 2.39.0 结果集）→ `allure serve/open`。

生成 `scripts/run_allure.py`（薄包装，避免用户记命令）：

```python
"""pytest（产 runs.jsonl）→ atlas allure（产 Allure 结果集）→ allure serve/open。"""
import argparse, subprocess, sys
from pathlib import Path

RUNS = Path("reports/runs.jsonl")
RESULTS = Path("reports/allure-results")
REPORT = Path("reports/allure-report")

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["test", "emit", "generate", "serve", "open"])
    ap.add_argument("--allure", default="allure", help="allure CLI 可执行文件名")
    ap.add_argument("-k", default="")
    args = ap.parse_args()

    if args.mode == "test":                          # 纯 pytest，产 runs.jsonl
        cmd = [sys.executable, "-m", "pytest", "-q"]
        if args.k: cmd += ["-k", args.k]
        return subprocess.call(cmd)
    if args.mode == "emit":                          # runs.jsonl → Allure 结果集（Atlas 直出）
        return subprocess.call([sys.executable, "-m", "atlas", "allure", str(RUNS),
                                "--out", str(RESULTS), "--clean"])
    if not RESULTS.exists():
        print(f"没有结果集 {RESULTS}，先 `python scripts/run_allure.py test` 再 `emit`", file=sys.stderr)
        return 2
    if args.mode == "generate":
        return subprocess.call([args.allure, "generate", str(RESULTS),
                                "-o", str(REPORT), "--clean"])
    if args.mode == "serve":
        return subprocess.call([args.allure, "serve", str(RESULTS)])
    return subprocess.call([args.allure, "open", str(REPORT)])   # open 读已生成报告

if __name__ == "__main__":
    raise SystemExit(main())
```

README 同时给**直连命令**（团队约束明确要求 `allure serve` / `allure open`）：
```bash
pytest -q                                                  # 纯 assert smoke，写 reports/runs.jsonl
atlas allure reports/runs.jsonl --out reports/allure-results --clean   # 方案 A 直出 Allure 2.39.0 结果集
allure serve reports/allure-results                        # 临时服务并浏览器打开
allure open reports/allure-report                          # 打开已生成报告
allure generate reports/allure-results -o reports/allure-report --clean
```

> 真实执行路径（需 `.env` 配 `DEMO_URL_SIT` 与会话）：`atlas run flows/smoke.yaml` 也会写
> `reports/runs.jsonl`，同样可 `atlas allure` 直出——骨架的 pytest smoke 只是提供了**离线**等价输入。

`.gitignore` 追加：`reports/allure-results/`、`reports/allure-report/`。

### 2.8 Allure 版本口径（**已按 gap5 方案 A 裁决**，见 §8）

- **默认不装 `allure-pytest`**（原 `allure-pytest==2.39.0` 版本不存在——allure-pytest 是 2.x 线，
  2.39.0 是 Allure CLI 版本；与其纠缠版本号，不如按方案 A 根本不引入该依赖）。
- 报告能力 = **纯 stdlib 直出 Allure 2.39.0 结果集**：`atlas allure <runs.jsonl> --out <allure-results>`
  （gap5 `allure_report.emit_allure`，读侧复用 `reporting` 的 `#112/#131` 容错纪律）。
- 生成骨架的 pyproject 依赖 = `atlas-test-platform` + `playwright/requests/PyYAML` + `pytest>=8`，
  **无 `--alluredir`**（结果不由 pytest 插件写）。
- **Allure CLI 2.39.0** 仍按 team-lead 要求文档化安装（Scoop/Choco/官网 zip）+ `allure --version` 校验。
- `allure-pytest` 仅作为 **gap5 P4 可选 `--via-pytest`**，届时才钉真实版本（如 `2.13.5`），默认关闭
  （AGENTS 红线 #4：引入浏览器驱动之外的运行时先问）。

### 2.9 生成器骨架（`src/atlas/scaffold.py`，init 部分）

```python
"""通用项目脚手架生成器；模板是数据（templates/），引擎不含业务字面量（红线 11）。"""
from __future__ import annotations
import shutil
from pathlib import Path

TEMPLATES = Path(__file__).parent / "templates"

class ScaffoldError(Exception):
    """用户输入/目标冲突 → CLI 走 rc 2（与 cli._user_error 同口径）。"""

def _render(text: str, ctx: dict) -> str:
    # 极简变量替换（{{key}}）；不用 jinja 以免引入大型框架依赖（AGENTS §4 Ask first）。
    for key, value in ctx.items():
        text = text.replace("{{" + key + "}}", str(value))
    return text

def _write_tree(dest: Path, template_dir: Path, ctx: dict, force: bool) -> list[str]:
    written = []
    for src in sorted(template_dir.rglob("*")):
        rel = src.relative_to(template_dir)
        dst = dest / rel
        if src.is_dir():
            dst.mkdir(parents=True, exist_ok=True); continue
        if dst.exists() and not force:
            raise ScaffoldError(f"目标已存在：{dst}（加 --force 覆盖）")
        dst.parent.mkdir(parents=True, exist_ok=True)
        content = _render(src.read_text(encoding="utf-8"), ctx)
        dst.write_text(content, encoding="utf-8")
        written.append(str(rel))
    return written

def init_project(args, config) -> tuple[int, dict]:
    dest = (args.dir or Path(f"./{args.name or 'atlas-project'}")).resolve()
    name = args.name or dest.name
    # 冲突防护：已是 Atlas 项目（有 .atlas-root）默认拒绝，避免覆盖他人工作
    if (dest / ".atlas-root").exists() and not args.force:
        raise ScaffoldError(f"{dest} 已是 Atlas 项目（含 .atlas-root）；--force 才覆盖")
    if dest.exists() and any(dest.iterdir()) and not args.force:
        raise ScaffoldError(f"{dest} 非空；--force 才写入")
    ctx = {
        "name": name, "domain": args.domain, "env_prefix": args.env_prefix,
        "allure_cli_version": args.allure_cli_version,   # 仅文档化 CLI 2.39.0；不装 allure-pytest
        "atlas_dep": (f'atlas-test-platform @ file:///{Path(args.atlas_path).resolve().as_posix()}'
                      if args.atlas_path else "atlas-test-platform"),
    }
    dest.mkdir(parents=True, exist_ok=True)
    files = _write_tree(dest, TEMPLATES / "project", ctx, args.force)
    # .atlas-root 最后写：中途失败不留「半成品却像完整项目」的假象
    (dest / ".atlas-root").write_text('atlas-root = "."\n', encoding="utf-8")
    files.append(".atlas-root")
    # .env 由 .env.example 复制（值留空，绝不写凭据——红线 1）
    env_example = dest / ".env.example"
    if env_example.exists():
        shutil.copyfile(env_example, dest / ".env")
    return 0, {"created": str(dest), "files": files,
               "next": build_next_steps(name, dest)}
```

`cli.py` 接线（在 `main()` 的 `if args.command == ...` 链里新增，与 doctor 同级）：

```python
if args.command == "init":
    from .scaffold import ScaffoldError, init_project
    try:
        code, out = init_project(args, config)
    except ScaffoldError as exc:
        return _user_error(exc)   # rc 2 口径，与 compile/run/machine 一致
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return code
```

### 2.10 init 红线遵守对照

| 红线 | 落实 |
|---|---|
| #1 禁凭据入库 | `.env`/`.env.example` 值全空，只留键名与占位注释；不写任何真实账号/密码。 |
| #11 引擎零业务字面量 | 模板用 `{{name}}/{{domain}}/{{env_prefix}}` 占位；默认 `demo`。生成器不内联客户名。 |
| #12 禁形式代码 | `smoke.yaml` 明标「需真实 env」；README 说明 doctor 预期 rc 1；不伪造 legacy 目录。 |
| 不覆盖他人工作 | 检测 `.atlas-root`/非空目录默认拒绝；`.atlas-root` 最后落盘。 |
| §4 Ask first | 不引入 jinja 等大型依赖；用标准库 + 字符串替换。 |

---

## 3. Gap 2：`atlas config add`（微服务配置生成器）

对应手册「**两种配置流程**」：
- **流程一（映射适配，零代码）**：新系统与现有插件形状一致（同 demo 家族）→ 只改 `ATLAS_*`。
- **流程二（插件适配，生成骨架）**：新系统结构不同 → 生成 `plugins/<domain>/` 并注册。

### 3.0 前置：通用插件加载缝（Gap 2 的唯一引擎级改动）

**问题**：`cli.py`/`runtime.py` 硬编码 `DemoPlugin()`（§0.4），外部项目无法在不改引擎的情况下
接入自有插件。要让「生成 `plugins/<domain>/` 且不碰引擎」成立，必须提供一个**通用加载器**
（非业务逻辑，符合红线 11 —— 它是「插件如何被发现」的机制，不含任何客户字面量）。

新增 `src/atlas/plugin_api.py`：

```python
"""通用插件解析：ATLAS_PLUGIN 选插件，默认 demo；引擎不含任何业务字面量。"""
from __future__ import annotations
import importlib, importlib.util, os, sys
from pathlib import Path

def resolve_plugin():
    """解析顺序（零回归优先）：
      1) 未设 ATLAS_PLUGIN → DemoPlugin()（仓库默认行为不变）
      2) ATLAS_PLUGIN 含路径分隔符/以 .py 结尾 → 按文件加载，取模块级 PLUGIN
      3) 否则 → import atlas.plugins.<name> 取 PLUGIN；再退 import <name> 取 PLUGIN
    错误一律点名 ATLAS_PLUGIN 的取值（#90/#103 归因口径）。"""
    name = os.environ.get("ATLAS_PLUGIN", "").strip()
    if not name:
        from .plugins.demo import DemoPlugin
        return DemoPlugin()
    module = _load_module(name)
    plugin = getattr(module, "PLUGIN", None) or getattr(module, "get_plugin", lambda: None)()
    if plugin is None:
        raise ImportError(f"插件 {name!r} 未导出 PLUGIN/get_plugin()（检查 ATLAS_PLUGIN 指向）")
    return plugin

def _load_module(name: str):
    path = Path(name)
    if name.endswith(".py") or "/" in name or "\\" in name:
        if not path.is_file():
            raise FileNotFoundError(f"ATLAS_PLUGIN 指向的插件文件不存在：{path}")
        spec = importlib.util.spec_from_file_location(path.stem, path)
        module = importlib.util.module_from_spec(spec); sys.modules[path.stem] = module
        spec.loader.exec_module(module)
        return module
    for candidate in (f"atlas.plugins.{name}", name):
        try:
            return importlib.import_module(candidate)
        except ModuleNotFoundError:
            continue
    raise ModuleNotFoundError(f"找不到插件 {name!r}（试过 atlas.plugins.{name} / {name}）")
```

消费点改造（**全部机械替换 `DemoPlugin()` → `resolve_plugin()`**，语义零变化）：
`cli.py:223, 132/139(_full_registry 仍可保留 Demo 用于离线校验)`, `runtime.py:46/100/103/129/141`。
为守住「未设 ATLAS_PLUGIN 时行为完全一致」，新增回归测：默认 `resolve_plugin()` 是 `DemoPlugin`
（防漂移，与 #121 面B 的「单一真相」同纪律）。

> 这是一次性、通用、可离线测试的改动；`make_demo_provider` 改为插件方法 `make_provider(config, api, division_code)`，
> `allowed_api_operations` 也走插件实例（已是 `DemoPlugin` 属性，天然满足）。

### 3.1 CLI 设计

```
atlas config add <domain>
    --root DIR                项目根（默认 cwd 向上找 .atlas-root；全局参数）
    --mapping                 流程一：只生成映射片段，不生成插件目录
    --out DIR                 项目根（默认当前目录）

设计稿曾拟但未实现（有意简化）：--env-prefix / --legacy-web / --api-file / --api-class / --api-spec / --app-header-key / --app-header-value / --tasks / --force。
```

`<domain>` 校验：`^[a-z][a-z0-9_]*$`（Python 包名合法），非法 → rc 2。

**输出**（stdout JSON）：
```json
{"domain": "acme", "plugins_dir": ".../plugins/acme",
 "files": ["__init__.py","plugin.py","tasks.py","probes.py","knowledge/session.json", ...],
 "activate": "ATLAS_PLUGIN=acme",           // 或 file: 路径
 "env_fragment": { "ATLAS_ENV_PREFIX": "ACME_", "ATLAS_LEGACY_PROJECT": "...", ... },
 "next": ["把 ATLAS_PLUGIN=acme 写进 .env，或设环境变量",
          "atlas doctor", "atlas tasks"]}
```

- 流程一（`--mapping`）：**不**建 `plugins/`，只输出 `env_fragment` 并（可选）写
  `.env.<domain>.example`，语义是「复用现有插件形状，仅换映射」。
- 流程二（默认）：生成 `plugins/<domain>/` + 输出激活方式（`ATLAS_PLUGIN=<domain>`）。

### 3.2 生成的文件清单（流程二）

```
<root>/plugins/<domain>/
├── __init__.py              # from .plugin import <Domain>Plugin; PLUGIN = <Domain>Plugin()
├── plugin.py                # 镜像 DemoPlugin 接口（§3.3）
├── tasks.py                 # 最小 Task 骨架（诚实 NotImplementedError，§3.4）
├── probes.py                # DataProbe 占位（CallableProbe + 未实现 find/create）
└── knowledge/
    ├── session.json         # {"account":"","ou":"","division_code":"","language":"English US"}
    ├── envs.json            # 7 环境（键前缀用默认 DEMO_；--env-prefix 未实现）
    ├── domains.json         # {"domains":{}} 空但合法
    ├── project_map.json     # 骨架
    ├── pitfalls.json        # {"pitfalls":[]} 空（新库无坑点，红线 10：不预置知识）
    ├── components.json      # {"components":{},"selectors":{}} 空
    └── contracts/core.json  # {"policy":"...","contracts":[]} 空（不伪造强契约，红线 12）
<root>/.env.<domain>.example # env 键清单（映射与激活说明）
```

### 3.3 `plugins/<domain>/plugin.py`（镜像 DemoPlugin 接口）

```python
"""{{domain}} 业务插件：结构镜像 DemoPlugin，接入靠 ATLAS_PLUGIN={{domain}}（不改引擎）。"""
from pathlib import Path
import os
from atlas.contracts import ContractRegistry
from atlas.dsl import TaskRegistry
from atlas.knowledge import KnowledgeBase, SelectorRegistry
from atlas.probes import DataProvider
from .tasks import core_tasks
from .probes import make_provider as _make_provider   # 需实现（见 probes.py）

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
        return KnowledgeBase(self.knowledge_root)

    def selectors(self) -> SelectorRegistry:
        return SelectorRegistry(self.knowledge().load("components"))

    def contracts(self) -> ContractRegistry:
        # 与 DemoPlugin 同款：私有→包内逐文件回退，两级皆缺显式报错（红线 7：静默空表掩盖缺陷）
        candidates = []
        if self.private_root:
            candidates.append(self.private_root / "contracts" / "core.json")
        candidates.append(self.knowledge_root / "contracts" / "core.json")
        for path in candidates:
            if path.exists():
                try:
                    return ContractRegistry.load(path)
                except (ValueError,):     # JSONDecode/UnicodeDecode 亦属损坏
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
```

> **契约（供测试与文档钉死）**：新插件必须实现 `name / allowed_api_operations / knowledge /
> selectors / contracts / session_defaults / register_tasks / make_provider` 八个成员——
> 与 `DemoPlugin` 一致，使 `resolve_plugin()` 的返回可被 `cli`/`runtime` 无差别消费。
> 运行时的 `resolve_plugin().make_provider(...)` 需替换 `runtime.py` 里对 `make_demo_provider` 的直接调用。

### 3.4 `plugins/<domain>/tasks.py`（最小 Task，诚实骨架）

```python
"""{{domain}} 最小 Task 骨架。perform 未实现 → NotImplementedError 点名；
禁止伪造「看起来完整」的实现（AGENTS 红线 12）。实现后把 read_only/intent 改准确。"""
from __future__ import annotations
from atlas.core import Channel, Task, TaskMetadata

SESSION_ENV_KEYS = {"account": "ATLAS_SESSION_ACCOUNT", "ou": "ATLAS_SESSION_OU",
                    "division_code": "ATLAS_SESSION_DIVISION", "language": "ATLAS_SESSION_LANGUAGE"}

def overlay_session(defaults: dict) -> dict:
    import os
    for key, env in SESSION_ENV_KEYS.items():
        value = os.environ.get(env, "").strip()
        if value:
            defaults[key] = value
    return defaults

class DomainTask(Task):
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

    def clone(self, **kwargs) -> "DomainTask":
        return type(self)(self.metadata, defaults=self.defaults, **kwargs)

    def perform(self, actor):
        # TODO(人工)：接真实页面库/接口。未实现就抛，绝不返回假成功（红线 7/12）。
        raise NotImplementedError(
            f"{self.function} 尚未实现：请在 plugins/{{domain}}/tasks.py 接入真实操作或页面库")

def _task(function, intent, *, requires=(), produces=(), channel=Channel.UI,
          read_only=False, defaults=None):
    return DomainTask(TaskMetadata(domain="{{domain}}", intent=intent, requires=tuple(requires),
        produces=tuple(produces), channel=channel, read_only=read_only, tags=(function,)),
        defaults=defaults)

def core_tasks(legacy_pages_path=None) -> list[DomainTask]:
    # 与 --tasks 参数对应；示例四个，用户按需增删。元数据必须与将来 perform 的读写一致。
    specs = [
        ("open_list",  "打开 {{domain}} 列表", (), ("list_ready",),  Channel.UI, True,  {}),
        ("click_new",  "进入新建页",          ("list_ready",), ("new_ready",), Channel.UI, False, {}),
        ("save",       "保存单据",            ("new_ready",), ("save_result",), Channel.UI, False, {}),
        ("wait_result","等待保存结果取单号",  ("save_result",), ("document_no",), Channel.UI, True, {}),
    ]
    return [_task(f, i, requires=r, produces=p, channel=c, read_only=ro, defaults=d)
            for (f, i, r, p, c, ro, d) in specs]
```

### 3.5 `plugins/<domain>/probes.py`

```python
"""{{domain}} 数据探针骨架。find 需真实只读实现；create 默认禁用（红线 3/16：非验证脚本不建单）。"""
from atlas.probes import CallableProbe, DataProvider
from atlas.failures import EnvBlocked

class DomainProbe(CallableProbe):
    def __init__(self, api, division_code, creator=None):
        self.api, self.division_code = api, division_code
        super().__init__("{{Domain}}Entity", self._find, creator)

    def _find(self, where):
        # TODO(人工)：接真实只读查询。没有就返回 None（诚实），不要伪造。
        return None

def make_provider(config, api, division_code) -> DataProvider:
    return DataProvider(config, [DomainProbe(api, division_code)])
```

### 3.6 `.env.<domain>.example`（env 键清单）

```bash
# {{domain}} 系统映射（合并进项目 .env 或设为环境变量）
# 激活插件（流程二）；流程一复用现有插件时不需要它
ATLAS_PLUGIN={{domain}}
# 凭据/URL 键前缀整体切换（URL 与凭据键一起换）
ATLAS_ENV_PREFIX={{env_prefix}}
# 旧项目资产映射
ATLAS_LEGACY_PROJECT=../<legacy-project>
ATLAS_LEGACY_WEB={{legacy_web}}
ATLAS_DEMO_API_PROJECT=../<legacy-api>
ATLAS_DEMO_API_FILE={{api_file}}
ATLAS_DEMO_API_CLASS={{api_class}}
ATLAS_DEMO_API_SPEC={{api_spec}}
ATLAS_APP_HEADER_KEY={{app_header_key}}
ATLAS_APP_HEADER_VALUE={{app_header_value}}
# 会话默认值（真实项目必须覆盖；密码走 {{env_prefix}}PWD_<账号大写>）
ATLAS_SESSION_ACCOUNT=
ATLAS_SESSION_OU=
ATLAS_SESSION_DIVISION=
ATLAS_SESSION_LANGUAGE=English US
# 私有知识目录（真实坑点/组件/坐标写这里，仓库零污染）
ATLAS_KNOWLEDGE_DIR=
```

### 3.7 config add 红线遵守对照

| 红线 | 落实 |
|---|---|
| #11 业务在插件，不进引擎 | 引擎只加**通用** `plugin_api.py`（发现机制，无字面量）；客户内容全在生成的 `plugins/<domain>/`。 |
| #10 无审核不写知识 | 生成的 `knowledge/*` 全为空（`pitfalls:[]` / `contracts:[]`），不预置任何知识；真实知识走 `atlas review` 人审。 |
| #12 不造形式代码 | Task `perform` 抛 `NotImplementedError` 点名；`allowed_api_operations` 空集；契约空表；README 明标 TODO。 |
| #3/#16 建单护栏 | 生成 `probes.py` 默认无 creator；接入需 `ScriptedCreator`/`make_scripted_creator`（已验证脚本）。 |
| 零回归 | 未设 `ATLAS_PLUGIN` → `resolve_plugin()` 仍 `DemoPlugin`，行为完全不变（新增回归测钉死）。 |

---

## 4. 两个命令与现有机制的完整关系图

```
atlas init ──生成──> 新项目(含 .atlas-root/.env/pyproject/flow/tests)
                         │ 复用（不复制源码）
                         ▼
            已安装的 atlas-test-platform 库
                         │
        ATLAS_* 映射 ────┤ 把虚构 demo 映射到真实项目
                         │
atlas config add ──生成──> plugins/<domain>/ (+ env 片段)
                         │
        ATLAS_PLUGIN=<domain> ──> resolve_plugin()  <── 引擎唯一新增通用缝
                         │
                         ▼
                cli/runtime 无差别消费（与内置 DemoPlugin 同接口）
```

- `atlas doctor`：新项目未接旧项目时 rc 1（诚实）；生成器 README 说明。
- `atlas compile`：`flows/smoke.yaml` 离线可过（`_full_registry` 含 `api_call`）。
- `atlas tasks`：默认列 demo Task；`ATLAS_PLUGIN=<domain>` 后列新插件的骨架 Task。

---

## 5. 关键代码骨架汇总（复用点标注）

| 新增/改动 | 文件 | 复用 | 说明 |
|---|---|---|---|
| **新增** 插件加载缝 | `src/atlas/plugin_api.py` | `DemoPlugin` | `resolve_plugin()`，默认 demo（零回归） |
| **新增** 生成器 | `src/atlas/scaffold.py` | `AtlasConfig`、`cli._user_error` | `init_project()` / `add_config()` |
| **新增** 模板数据 | `src/atlas/templates/project/**`、`templates/plugin/**` | `.env.example`、`.gitignore` | package-data，`{{var}}` 占位 |
| **改** package-data | `pyproject.toml` `[tool.setuptools.package-data]` | — | 加 `"atlas" = ["templates/**"]` |
| **改** CLI 子命令 | `src/atlas/cli.py` `_parser()` | `_user_error` | 加 `init` / `config add`；`config` 用嵌套 subparser |
| **改** 插件消费点 | `cli.py`、`runtime.py` | — | `DemoPlugin()` → `resolve_plugin()`；provider 走插件方法 |
| **改** 文档 | `README.md`/`AGENTS.md`/`CLAUDE.md` | — | AGENTS §5：改动行为后同步（收工时做） |

**复用清单（能 import 的别重造）**：
- `AtlasConfig.from_root()`（root/.env/BOM 兜底）——生成的 `.env` 直接吃它。
- `as_uri()` / `pathlib` 处理 `--atlas-path` 的跨平台 `file://`。
- `config.ENV_PREFIX_DEFAULT`、`ENV_URL_SUFFIXES`——env 片段键名与引擎**同源**，不手写。
- `cli._user_error`——rc 2 友好报错统一入口。
- `FlowCompiler`/`_full_registry`——生成 flow 的离线校验（测试里直接用）。
- `BrowserSession`——smoke 的 Playwright 底座（proven by `examples/smoke_browser.py`）。

---

## 6. Allure 2.39.0 接入小结（**方案 A**，对齐 gap5）

1. **依赖**：生成项目 `dependencies` 含 `pytest>=8`，**不装 `allure-pytest`**；Allure 结果由
   `atlas allure`（纯 stdlib，gap5 `allure_report.py`）从 `reports/runs.jsonl` 直出。
2. **pytest 配置**：`[tool.pytest.ini_options]` 只设 `testpaths=["tests"]`，**无 `--alluredir`**。
3. **产结果**：`pytest -q` 跑纯 assert 的 smoke（其中一条驱动引擎写 `reports/runs.jsonl`）；
   随后 `atlas allure reports/runs.jsonl --out reports/allure-results --clean` → Allure 2.39.0 结果集。
4. **看报告**：`allure serve reports/allure-results` / `allure open reports/allure-report` /
   `allure generate ... --clean`；封装在 `scripts/run_allure.py`（`test/emit/generate/serve/open`）。
5. **Allure CLI 2.39.0**：README 文档化安装与 `allure --version` 校验；`.gitignore` 忽略结果/报告目录。
6. **不引入运行时依赖**：默认纯 stdlib（AGENTS 红线 #4）；`allure-pytest` 属 gap5 P4 可选 `--via-pytest`，
   显式开启并文档标注额外依赖。引擎/骨架零新增运行时。

---

## 7. 测试计划（离线、无网络/浏览器/凭据，窄接口 fake）

### 7.1 Gap 1：`tests/test_init_scaffold.py`
1. `main(["--root", tmp, "init", str(tmp/"proj"), "--system", "ics", "--module", "so"])` → rc 0；断言生成
   `.atlas-root/.env/.env.example/.gitignore/pyproject.toml/README.md/flows/smoke.yaml/
   tests/test_smoke.py/scripts/run_allure.py` 全在。
2. 生成 `pyproject.toml` 含 `pytest>=8`、`atlas-test-platform`；**不含 `allure-pytest`、不含 `--alluredir`**
   （方案 A：结果由 `atlas allure` 直出）。
3. `.env` 与 `.env.example`：所有 `=` 右侧为空或注释；**断言不含任何非空凭据**（正则扫 `PASSWORD=` 后非空）。
4. 生成的 `.py` 全部 `compileall` 通过（语法有效）。
5. `atlas compile <proj>/flows/smoke.yaml`（`--root <proj>`）→ rc 0（离线结构校验）。
6. 生成项目里 `ATLAS_PLUGIN` 未设时 `resolve_plugin()` 仍为 `DemoPlugin`（零回归）。
7. 冲突防护：目标已有 `.atlas-root` → rc 2 且不改动；`--force` → rc 0。
8. `--interactive` 用 monkeypatch 喂 stdin；默认（不传）不读 stdin（断言无阻塞）。
9. 「不用业务字面量」：生成物不出现 `CUST0000001` 等 demo 业务值（grep）。
10. **Allure 端到端（方案 A，条件）**：在生成项目里 `pytest -q`（跑纯 assert smoke，产
    `reports/runs.jsonl`）→ `atlas allure reports/runs.jsonl --out <tmp>/ar --clean` → 断言
    `<tmp>/ar/*-result.json` 至少 1 个、且 `status` 与 run 一致。
    需 Playwright/浏览器；环境不可用时 `pytest.skip` 并打印原因，**不伪装通过**（autonomous-execution 纪律）。
11. 生成 `pyproject.toml` 断言**不出现** `allure-pytest` / `--alluredir`（防方案 A 被改回）。

### 7.2 Gap 2：`tests/test_config_add.py`
1. `main(["--root", tmp, "config", "add", "acme"])` → rc 0；断言 `plugins/acme/` 下
   `__init__.py/plugin.py/tasks.py/probes.py/knowledge/*.json` 与 `.env.acme.example` 全在。
2. 非法 domain（`Acme`/`1ac`/`a-b`）→ rc 2，stderr 一行（`_user_error` 口径）。
3. 生成的 knowledge JSON 全为合法 JSON 且经 `KnowledgeBase` 加载为 `{}`/空表；
   `pitfalls.json` 为空数组、`contracts/core.json` 的 `contracts` 为空（红线 10/12）。
4. `resolve_plugin("acme")`（设 `ATLAS_PLUGIN=acme` + `PYTHONPATH` 含 `<tmp>`）→ 返回对象具有
   八个契约成员；`register_tasks(TaskRegistry())` 成功；`atlas tasks`（该 root）列出骨架 Task，
   每条 metadata 合法（domain 非空、produces 为 tuple、channel ∈ {ui,api,auto}）。
5. 骨架 Task 的 `perform` 抛 `NotImplementedError` 且消息点名函数名（**不伪造成功**）。
6. `--mapping` → 不生成 `plugins/`，输出 `env_fragment` 含 `ATLAS_ENV_PREFIX` 等键。
7. 零回归：`ATLAS_PLUGIN` 未设 → `resolve_plugin() is DemoPlugin` 实例；`atlas doctor`/`atlas tasks`
   默认输出与改动前一致（用现有 CLI 测做基线）。
8. 覆盖防护：`plugins/acme` 已存在 → rc 2；`--force` → rc 0。
9. env 片段键名与 `config.py` 同源：`ATLAS_ENV_PREFIX`/`ATLAS_LEGACY_*`/`ATLAS_DEMO_API_*`/
   `ATLAS_APP_HEADER_*`/`ATLAS_SESSION_*` 均在（防手写漂移）。
10. 失败/异常归因：坏 `ATLAS_PLUGIN`（不存在路径）→ 友好报错点名 env 值（rc 2，不裸 traceback）。

### 7.3 通用缝（可并入 7.2）
- `resolve_plugin()`：未设→Demo；`atlas.plugins.demo`→Demo；外部 `.py` 路径→加载；不存在→点名。

### 7.4 门禁（遵 AGENTS §2）
```bash
PYTHONPATH=src python -m pytest -q
PYTHONPATH=src python -m compileall -q src
PYTHONPATH=src python -m atlas doctor
```
新增测试不得依赖真实网络/浏览器/凭据（除 7.1-10 有条件 skip）。

---

## 8. 待确认（**已裁决**，2026-11-04 team-lead）

1. **Allure 版本口径（§2.8）—— 已裁决**：统一到 gap5 **方案 A**。默认**不装 `allure-pytest`**；
   Allure 2.39.0 结果由 `atlas allure`（纯 stdlib）从 `reports/runs.jsonl` 直出。
   `allure-pytest` 仅 gap5 P4 可选 `--via-pytest` 时才钉真实版本（如 2.13.5）。Allure CLI 2.39.0 文档化。
2. **插件加载缝（§3.0）—— 已裁决：接受**。`plugin_api.resolve_plugin()` 是本轮**唯一引擎级改动**：
   cli.py/runtime.py 里 `DemoPlugin()` → `resolve_plugin()`，机械替换、零业务语义变化；未设
   `ATLAS_PLUGIN` 时行为**完全一致**（含 `make_provider` 语义），必须新增回归测钉死零回归。
3. **依赖形态（§2.3）—— 已确认**：默认 `atlas-test-platform`（需先 editable 安装），
   `--atlas-path` 支持 `file://`。无需内网私有索引名。
4. **`--interactive` —— 已确认保留**：默认命令式（CI 安全），对话式仅显式开启。

> 以上均以 `docs/gaps/gap5-allure.md` 为**权威口径**对齐。

---

## 9. 交付边界声明（诚实）

- 本文档只做**设计**，未改任何源码。
- 生成物中的 Task/Probe 是**骨架**，默认抛 `NotImplementedError`——这是有意为之（红线 12），
  不声称「已能跑真实业务」。真正可跑的只有 `tests/test_smoke.py`（自包含浏览器冒烟 + 离线 compile +
  产 `runs.jsonl` 供 `atlas allure`）。
- Allure 由 **gap5 方案 A** 实现（`atlas allure` 从 `runs.jsonl` 直出结果集，纯 stdlib）；
  骨架默认**不装 `allure-pytest`**，Allure 结果不由 pytest 插件写。
