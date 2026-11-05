"""命令行只暴露确定性动作；不向第三方 AI 自动上传项目数据。"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

import yaml
from . import __version__
from .composition import catalog, compose_for_goal
from .config import AtlasConfig, load_dotenv
from .dsl import FlowCompiler, TaskRegistry
from .legacy import LegacyAdapter
from .plugin_api import resolve_plugin
from .plugins.demo import core_tasks
from .runtime import DemoRuntime, check_demo_api

def _parser() -> argparse.ArgumentParser:
    # 全局参数用 parents 共享：`atlas run x.yaml --mode debug`（文档口径）与
    # `atlas --mode debug run x.yaml` 都必须可用——真机冒烟抓到只认前者会 exit 2。
    global_args = argparse.ArgumentParser(add_help=False)
    # default=SUPPRESS：否则子解析器的默认值会覆盖全局位已设值（argparse 经典坑）。
    # 缺省语义由 main() 的 getattr 兜底。
    global_args.add_argument("--root", type=Path, default=argparse.SUPPRESS)
    global_args.add_argument("--env", default=argparse.SUPPRESS, choices=["sit", "uat-eu", "uat-amer", "uat-hk", "prod-eu", "prod-amer", "prod-hk"])
    global_args.add_argument("--mode", default=argparse.SUPPRESS, choices=["run", "debug", "heal"])
    global_args.add_argument("--headed", action="store_true", default=argparse.SUPPRESS,
                             help="有头模式，供 DSL 调试")
    parser = argparse.ArgumentParser(prog="atlas", description="知识驱动的 UI/API 测试自动化平台",
                                      parents=[global_args])
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)

    def add(name: str, **kw):
        return commands.add_parser(name, parents=[global_args], **kw)

    add("doctor", help="检查依赖、配置与外部资产（不登录）")
    init_parser = add("init", help="生成新 Atlas 项目骨架（含 .atlas-root/.env/pyproject/flow/tests）")
    init_parser.add_argument("name", help="项目名（写进 pyproject/README）")
    init_parser.add_argument("--system", required=True, help="系统名（描述性标签）")
    init_parser.add_argument("--module", required=True, help="模块名（描述性标签）")
    init_parser.add_argument("--atlas-path", type=Path, default=None,
                             help="可选：把 Atlas 仓库以 file:// 依赖钉进生成项目")
    init_parser.add_argument("--interactive", action="store_true",
                             help="对话式补问缺省值（CI 不设即零交互）")
    init_parser.add_argument("--out", type=Path, default=Path("."),
                             help="输出目录（默认当前目录）")
    config_parser = add("config", help="微服务配置生成器")
    config_sub = config_parser.add_subparsers(dest="config_command", required=True)
    config_add_parser = config_sub.add_parser("add", parents=[global_args],
                                              help="生成插件骨架或映射片段")
    config_add_parser.add_argument("domain", help="业务域标识（^[a-z][a-z0-9_]*$）")
    config_add_parser.add_argument("--mapping", action="store_true",
                                   help="只出 ATLAS_* 映射片段（零代码）")
    config_add_parser.add_argument("--out", type=Path, default=Path("."),
                                   help="项目根（默认当前目录）")
    add("tasks", help="输出核心 Task 元数据")
    compose_parser = add("compose", help="验证核心 Task 依赖拼装")
    compose_parser.add_argument("--goal", default="document_no")
    flow = add("run", help="执行声明式 DSL（需已登录的 Actor，使用 Python 入口注入）")
    compile_cmd = add("compile", help="离线编译校验 flow YAML（不登录、不执行）")
    compile_cmd.add_argument("flow", type=Path)
    flow.add_argument("flow", type=Path)
    flow.add_argument("--account", default=None, help="缺省取业务插件 session 默认账号")
    flow.add_argument("--ou", default=None, help="缺省取业务插件 session 默认 OU")
    flow.add_argument("--language", default=None, help="UI 语言，缺省取业务插件默认（选择器优先英文）")
    flow.add_argument("--division", default=None, help="接口 divisionCode，缺省取业务插件默认")
    flow.add_argument("--no-login", action="store_true", help="仅使用已有 storage state；不自动登录")
    flow.add_argument("--create-quota", type=int, default=0, help="本次允许自动建单数，默认 0")
    flow.add_argument("--bu", default="", help="应用已批准 BU overlay 坐标到会话默认值（ATLAS_SESSION_* env 优先）")
    legacy = add("legacy", help="原样执行 legacy-suite pytest 用例")
    legacy.add_argument("node", nargs="?", default=None,
                        help="pytest 节点；缺省取 ATLAS_LEGACY_TEST_NODE 或内置示例")
    # --headed 由 global_args 统一提供（子命令前后均可），不再局部重复定义
    legacy.add_argument("--keyword", default="", help="pytest -k 过滤，例如 test_tc7_ or test_tc8_")
    legacy.add_argument("--dry-run", action="store_true")
    sm = add("machine", help="校验状态机 YAML 并输出目标路径（不执行）")
    sm.add_argument("file", type=Path)
    sm.add_argument("--target", default="", help="目标状态；省略则只做结构校验")
    contracts_cmd = add("contracts", help="查看契约分级覆盖情况")
    contracts_cmd.add_argument("--validate", action="store_true",
                               help="只读结构自校验契约定义：报告无法解析的契约文件、以及空 path/name 等"
                                    "「在列表里冒充覆盖率、运行时却静默跳过」的永不生效项（不发起真实请求）")
    selectors = add("selectors", help="查询 Demo 选择器注册表")
    selectors.add_argument("component", nargs="?", default="")
    agent = add("agent", help="Agent 规划/风险评估（默认只规划不执行）")
    agent.add_argument("--goal", default="document_no", help="目标产出键，如 document_no")
    agent.add_argument("--execute", action="store_true", help="真正执行；需风险允许或加 --approve")
    agent.add_argument("--approve", action="store_true", help="人工批准超过自动阈值的高风险执行")
    agent.add_argument("--max-auto-risk", default="read_only",
                       choices=["read_only", "low", "medium", "high"])
    report = add("report", help="将 Atlas JSONL 转为 runlog 步骤格式")
    report.add_argument("input", type=Path)
    report.add_argument("output", type=Path)
    summary = add("summary", help="聚合运行记录：状态、失败类、最慢步骤")
    summary.add_argument("input", type=Path, nargs="?", default=Path("reports/runs.jsonl"))
    allure_cmd = add("allure", help="把 runs.jsonl 转 Allure 2.39.0 结果集（纯 stdlib，不拉浏览器）")
    allure_cmd.add_argument("input", type=Path, nargs="?", default=None,
                            help="runs.jsonl 路径（默认 <root>/reports/runs.jsonl）")
    allure_cmd.add_argument("--out", type=Path, default=None,
                            help="输出目录（默认 <root>/reports/allure-results）")
    allure_cmd.add_argument("--clean", action="store_true",
                            help="先清空结果集里 Atlas 前缀文件（默认即清，仅删 atlas-* 不误删用户附件）")
    allure_cmd.add_argument("--serve", action="store_true",
                            help="生成后打印 allure serve 命令（不自行拉起浏览器/网络）")
    allure_analyze_cmd = add("allure-analyze",
                             help="解析 Allure 结果集：分类汇总 + 自愈提案/审核塔草稿（Gap5 §4 汇入侧）")
    allure_analyze_cmd.add_argument("results_dir", type=Path,
                                    help="Allure 结果集目录（含 *-result.json）")
    allure_analyze_cmd.add_argument("--to-tower", action="store_true",
                                    help="未命中项登记 pending_review 进审核塔（走 ExperienceJournal）")
    allure_analyze_cmd.add_argument("--format", default="json", choices=["json", "text"],
                                    help="输出格式：json（默认）或 text（人读版）")
    knowledge = add("knowledge", help="查询知识库")
    knowledge.add_argument("query", nargs="?", default="")
    recycle = add("recycle", help="列出本次运行自动创建的单据（不自动删除）")
    recycle.add_argument("--input", type=Path, default=None,
                         help="回收清单文件（默认 root 下 reports/recycle_list.jsonl，与 run 落盘同源）")
    recycle.add_argument("--cleanup", action="store_true",
                         help="尝试删除造的单（写操作，需人审且非生产）")
    review = add("review", help="审核吞吐入口：草稿队列→证据卡片→批准/驳回→留痕")
    review.add_argument("--stats", action="store_true", help="队列与吞吐统计")
    review.add_argument("--approve", default=None, metavar="ID",
                        help="按 id/前缀批准：坑点进知识，选择器候选进组件库（按 kind 分发）")
    review.add_argument("--remedy", default="", help="批准时的修复动作（坑点）或选择器本身（候选）")
    review.add_argument("--component", default="", help="批准 selector_candidate：写入的组件名")
    review.add_argument("--element", default="", help="批准 selector_candidate：写入的元素名")
    review.add_argument("--reject", default=None, metavar="ID", help="按 id/前缀驳回")
    review.add_argument("--reject-kind", default="",
                        choices=["", "pitfall", "selector_candidate", "trace_step", "bu_overlay"],
                        help="整类驳回（原因必填），批量清塔")
    review.add_argument("--template", type=Path, default=None, metavar="FILE",
                        help="导出人可编辑的决策模板 JSONL（配合 --kind 选类）")
    review.add_argument("--apply", type=Path, default=None, metavar="FILE",
                        help="执行填好的决策模板：逐条批准分发留痕，单行失败不拦其余")
    review.add_argument("--reason", default="", help="驳回原因")
    review.add_argument("--kind", default="", choices=["", "pitfall", "selector_candidate", "trace_step", "bu_overlay"],
                        help="只看某一类待审草稿")
    review.add_argument("--group", action="store_true",
                        help="聚类视图：批量类按证据源压成紧凑表，坑点保留全卡片")
    explore = add("explore", help="开 CDP 探索会话（非生产）供外部大脑/playwright-cli attach；或编译 trace 为草稿")
    explore.add_argument("--url", default=None, help="探索起点，缺省为登录后的页面")
    explore.add_argument("--account", default=None, help="探索账号（建议只读角色）")
    explore.add_argument("--trace", type=Path, default=None, help="trace 输出路径")
    explore.add_argument("--compile", type=Path, default=None, metavar="TRACE",
                         help="把 trace.jsonl 编译成 pending_review 草稿（不开浏览器）")
    overlay = add("overlay", help="BU 坐标（租户=坐标不是分支）：diff 与进塔；批准走 atlas review")
    overlay.add_argument("file", type=Path, help='{"BU名": {坐标键: 值}}；未验证 BU 请显式标注 sample')
    overlay.add_argument("--diff", nargs=2, default=None, metavar=("BASE_BU", "OTHER_BU"),
                         help="只列坐标差异，不写塔")
    overlay.add_argument("--to-tower", action="store_true", help="差异逐键生成 bu_overlay pending_review 草稿")
    gen_parser = add("gen", help="接口文档→Atlas 测试代码生成（只生成不执行，红线 #9）")
    gen_parser.add_argument("source", type=Path, help="文档路径（.md/.json/.yaml/.txt）")
    gen_parser.add_argument("--source-type", default="auto",
                            choices=["auto", "openapi", "swagger", "markdown", "text"],
                            help="文档格式（默认按内容嗅探）")
    gen_parser.add_argument("-o", "--out", type=Path, default=None,
                            help="产物目录（默认 exports/gen/<flow_id>/）")
    gen_parser.add_argument("--flow-id", default="", help="flow id（默认由文档标题/路径派生）")
    gen_parser.add_argument("--scenario", default="auto", choices=["auto", "a", "b", "c", "d"],
                            help="场景：a单接口/b链路/c批量/d写操作前置")
    gen_parser.add_argument("--level", default="auto", choices=["auto", "minimal", "high", "core"],
                            help="契约等级（core 需文档显式标注，命令行不能强升 core）")
    gen_parser.add_argument("--headers-file", default="",
                            help="JSON/YAML 全局头（值写成 ${ENV} 占位）")
    gen_parser.add_argument("--plugin", default="demo", help="flow 的 plugin 字段")
    gen_parser.add_argument("--naming", default="resp_{n}_{slug}",
                            help="save_as 命名模板")
    gen_parser.add_argument("--check-only", action="store_true",
                            help="只解析+编译自检，不写文件（CI 门禁）")
    gen_parser.add_argument("--strict", action="store_true",
                            help="低置信度/占位符未接 env/待填必填 → rc 2")
    gen_parser.add_argument("--force", action="store_true",
                            help="覆盖已存在同 id 产物（默认拒绝）")
    gen_parser.add_argument("--to-tower", action="store_true",
                            help="生成摘要登记 pending_review 进审核塔（trace_step）")
    gen_parser.add_argument("--with-pytest", action="store_true",
                            help="额外生成 pytest+allure 适配（需 allure-pytest 依赖）")
    review_code_parser = add("review-code",
                              help="代码质量评审：扫描 pytest/DSL/契约，输出含评分报告（Allure 2.39.0 风格）")
    review_code_parser.add_argument("paths", nargs="+", type=Path,
                                    help="评审目标（文件/目录，可多个）")
    review_code_parser.add_argument("--kind", default="auto",
                                    choices=["auto", "pytest", "dsl", "contract"],
                                    help="目标类型（默认 auto 按扩展名识别）")
    review_code_parser.add_argument("--review-mode", default="static",
                                    dest="review_mode",
                                    choices=["static", "knowledge"],
                                    help="评审模式（默认 static：纯离线规则扫描；knowledge：+选择器注册表；ai 未实现）")
    review_code_parser.add_argument("--format", default="allure",
                                    choices=["allure", "json", "text"],
                                    help="输出格式（默认 allure）")
    review_code_parser.add_argument("--out", type=Path, default=None,
                                    help="Allure 结果集输出目录（默认 reports/review-code-results）")
    review_code_parser.add_argument("--config", type=Path, default=None,
                                    help="reviewcode.json 配置文件（权重/阈值/规则开关）")
    review_code_parser.add_argument("--fail-under", type=int, default=None,
                                    help="总分阈值，低于即门禁失败 rc 1")
    review_code_parser.add_argument("--baseline", type=Path, default=None,
                                    help="基线文件（上一版用例或 runs.jsonl）做差分侦测")
    review_code_parser.add_argument("--to-tower", action="store_true",
                                    help="findings 进审核塔（kind=code_review, pending_review）")
    return parser

def ensure_utf8_streams() -> None:
    """Windows 控制台默认 GBK，中文 intent/异常会乱码；流已配好编码或不可重配时不动。"""
    import sys
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name)
        if (hasattr(stream, "reconfigure")
                and getattr(stream, "encoding", "").lower() not in ("utf-8", "utf8")):
            try:
                stream.reconfigure(encoding="utf-8")
            except (OSError, ValueError):
                pass

def _full_registry() -> TaskRegistry:
    """compile 校验需要完整 registry（含 runtime 侧通用 Task），与 DemoRuntime.registry() 一致。"""
    registry = TaskRegistry()
    resolve_plugin().register_tasks(registry)
    from .plugins.demo.legacy_case import LegacyCaseTask
    from .runtime import DemoApiOperation, LegacyStepTask
    from .tasks import AssertApi, CallEndpoint, UiAction
    registry.register("api_call", lambda **kwargs: CallEndpoint(**kwargs))
    registry.register("ui_action", lambda **kwargs: UiAction(**kwargs))
    registry.register("demo_api", lambda **kwargs: DemoApiOperation(**kwargs))
    registry.register("assert_api", lambda **kwargs: AssertApi(**kwargs))
    registry.register("legacy_step", lambda **kwargs: LegacyStepTask(**kwargs))
    registry.register("legacy_case", lambda **kwargs: LegacyCaseTask(**kwargs))
    return registry

def _read_text_safe(path: Path) -> str | None:
    """读诊断用文本资产；present-but-unreadable（非 UTF-8/权限/IO）返回 None，由调用方判为
    「无法确认」而非让 `atlas doctor` 抛 UnicodeDecodeError 崩掉整个诊断（doctor 的职责是汇报状态）。"""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

def _compose_cmd_matches(root: Path) -> bool:
    """compose 与 Dockerfile 的 python 命令必须一致（python3 对 python3）。

    背景：Dockerfile 基于 debian 系时只有 python3；compose 若写 python，
    回退基础镜像后容器内直接报 command not found。
    """
    compose = root / "docker-compose.yml"
    dockerfile = root / "Dockerfile"
    if not compose.exists() or not dockerfile.exists():
        return True
    df_text = _read_text_safe(dockerfile)
    text = _read_text_safe(compose)
    if df_text is None or text is None:
        return False   # 资产存在却读不出（非 UTF-8/权限）：无法确认一致 → 判不一致，不裸崩
    df_python = "python3" if "python3 -m pytest" in df_text else "python"
    for token in ("python -m pytest", "python3 -m pytest"):
        if token in text and not token.startswith(df_python):
            return False
    return True

def _user_error(exc: Exception) -> int:
    """CLI 用户输入边界的统一友好报错口径（与 overlay/recycle/review/explore 一致）：
    把「读用户传入文件/解析 YAML/引用未注册 Task」这类**输入错误**转成一行 stderr + rc 2，
    而不是抛裸 traceback。rc 2 = 用户/数据问题，区别于运行时执行失败；不吞异常，消息原样可见。"""
    print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
    return 2

def _validate_contracts(plugin) -> int:
    """契约定义的只读结构自校验（不发起任何真实请求）。

    为什么要它：`atlas contracts` 的清单会把每条契约都算进覆盖率，但 `Contract.validate()`
    对 `path` 为空的契约直接 `return`（contracts.py:40）——这类条目**在列表里冒充覆盖率、
    运行时却静默跳过、永不生效**，正撞红线 #7/#12（不得有看似完整实则不校验的断言层）。
    另外真实项目用 ATLAS_KNOWLEDGE_DIR 私有覆盖契约时，手改坏的 core.json 会让运行时/展示
    裸崩；这里把解析失败也变成可读报告。返回码：全部 OK=0，有问题=2（不改判定、只点名）。"""
    try:
        registry = plugin.contracts()
    except Exception as exc:   # 私有/包内契约文件写坏：给可读报告，不裸 traceback（只读诊断，不吞成通过）
        print(json.dumps({"ok": False, "source": "unresolved",
                          "error": f"契约文件无法解析：{type(exc).__name__}: {exc}"},
                         ensure_ascii=False, indent=2))
        return 2
    problems = []
    for method, path in sorted(registry.contracts):
        contract = registry.contracts[(method, path)]
        label = contract.name or f"{method} {path or '(空 path)'}"
        if not contract.path:
            problems.append(f"{label}: path 为空 → validate() 会静默跳过，该契约永不生效")
        if not contract.name:
            problems.append(f"{method} {path}: name 为空 → 校验失败信息无法归因")
    report = {"ok": not problems, "total": len(registry.contracts), "levels": registry.stats(),
              "problems": problems}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not problems else 2


def main(argv: list[str] | None = None) -> int:
    ensure_utf8_streams()
    args = _parser().parse_args(argv)
    # init / config add 不依赖 AtlasConfig/plugin 装配（新建项目时还没有 .atlas-root）
    if args.command == "init":
        from .scaffold import ScaffoldError, init_project
        try:
            out = init_project(args.name, args.system, args.module,
                              atlas_path=args.atlas_path,
                              interactive=args.interactive,
                              out_dir=args.out)
        except ScaffoldError as exc:
            return _user_error(exc)   # rc 2 口径，与 compile/run/machine 一致
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    if args.command == "config":
        from .config_add import ConfigAddError, add_config
        try:
            out = add_config(args.domain, mapping_only=args.mapping, out_dir=args.out)
        except ConfigAddError as exc:
            return _user_error(exc)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    # 全局参数在子命令前后都合法（SUPPRESS 默认）：取值走 getattr 兜底
    root = getattr(args, "root", Path.cwd())
    overrides = {key: value for key, value in {"env": getattr(args, "env", None),
                                               "mode": getattr(args, "mode", None)}.items()
                 if value}
    if getattr(args, "headed", False):
        overrides["headless"] = False
    # .env 编码坏/ATLAS_ENV 非法 = 纯用户输入问题（与 #79/#80 口径一致）：一行 stderr + rc 2。
    # 旧实现裸崩 traceback——真实踩点：PowerShell 默认 UTF-16 写 .env 后所有 atlas 命令全崩，
    # 报错信息（UnicodeDecodeError）不指向 .env 文件本身，人无从下手。
    try:
        config = AtlasConfig.from_root(root, **overrides)
    except ValueError as exc:
        return _user_error(exc)
    try:
        plugin = resolve_plugin()
    except ValueError as exc:
        # ATLAS_PLUGIN 指到未注册 domain 是用户输入问题：一行 stderr + rc 2（#79/#80 口径）
        return _user_error(exc)
    if args.command == "tasks":
        print(json.dumps(catalog(core_tasks()), ensure_ascii=False, indent=2))
        return 0
    if args.command == "compose":
        from .failures import FlowCompositionError
        try:
            selected = compose_for_goal(core_tasks(), args.goal)
        except FlowCompositionError as exc:
            return _user_error(exc)   # 目标不可拼装=用户输入问题，友好 rc 2，与 compile/run 同口径
        for task in selected:
            print(task.name)
        return 0
    if args.command == "knowledge":
        rows = plugin.knowledge().pitfalls()
        if args.query:
            rows = [row for row in rows if args.query.lower() in json.dumps(row, ensure_ascii=False).lower()]
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    if args.command == "agent":
        from .agent import ApprovalRequired, AtlasAgent, RiskLevel
        from .failures import FlowCompositionError
        agent = AtlasAgent(production=config.is_production,
                           max_auto_risk=RiskLevel(args.max_auto_risk),
                           journal=config.reports_dir / "agent_decisions.jsonl")
        tasks = core_tasks()
        if not args.execute:
            try:
                proposal = agent.propose(tasks, args.goal)   # 规划期就拼不出目标 → 输入错误，友好 rc 2
            except FlowCompositionError as exc:
                return _user_error(exc)
            print(json.dumps(proposal, ensure_ascii=False, indent=2))
            return 0
        # #103：同 run 口径——缺 URL/路径输入错误 rc 2，且在开浏览器/登录之前拒绝。
        try:
            load_dotenv(config.legacy_project)
            if not config.base_url:
                raise ValueError(f"缺少 {config.env} 对应的 {config.env_url_key} 配置")
            config.validate_base_url(config.base_url)
            check_demo_api(config)
            config.check_legacy_paths()   # #103 延伸：坏 legacy 路径也在浏览器/登录前 rc 2
        except (OSError, ValueError) as exc:
            return _user_error(exc)
        with DemoRuntime(config) as runtime:
            try:
                decision = agent.run(tasks, args.goal, runtime.actor, approved=args.approve)
            except ApprovalRequired as exc:
                print(json.dumps({"approved": False, "reason": str(exc)}, ensure_ascii=False, indent=2),
                      file=sys.stderr)
                return 3
            except FlowCompositionError as exc:
                return _user_error(exc)
        print(json.dumps(decision.as_dict(), ensure_ascii=False, indent=2))
        return 0
    if args.command == "machine":
        from .state_machine import StateMachine
        try:
            machine = StateMachine.load(args.file)
            if args.target:
                result = {"target": args.target, "tasks": machine.path(args.target)}
            else:
                result = {"initial": machine.initial, "states": sorted(machine.states),
                          "terminal": sorted(machine.terminal)}
        except (OSError, ValueError, yaml.YAMLError) as exc:
            return _user_error(exc)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "contracts":
        if getattr(args, "validate", False):
            return _validate_contracts(plugin)
        registry = plugin.contracts()
        report = {"levels": registry.stats(), "total": len(registry.contracts),
                  "contracts": [{"name": c.name, "method": c.method, "path": c.path, "level": c.level}
                                for c in registry.contracts.values()]}
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    if args.command == "selectors":
        registry = plugin.selectors()
        if args.component:
            print(json.dumps(registry.find(args.component), ensure_ascii=False, indent=2))
        else:
            print(json.dumps({"components": registry.names(), "without_selectors": registry.missing()},
                             ensure_ascii=False, indent=2))
        return 0
    if args.command == "summary":
        from .reporting import summarize
        try:
            result = summarize(args.input)
        except (OSError, ValueError) as exc:
            return _user_error(exc)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "allure":
        from .allure_report import emit_allure
        # 默认输出与 config.reports_dir 同源（不赌 CWD）；输入缺省 <root>/reports/runs.jsonl
        runs = args.input if args.input is not None else config.reports_dir / "runs.jsonl"
        out = args.out if args.out is not None else config.reports_dir / "allure-results"
        try:
            # #139：相对附件按项目根解析（engine 落 runs.jsonl 的就是 root 相对路径）；
            # 旧实现不传，消费侧回落 runs_path.parent=reports/ 做基准——截图明明在
            # root/artifacts/snapshots/ 却假报「无法读取」生成占位（假阴性证据）。
            result = emit_allure(runs, out, clean=True, attachments_dir=config.root)
        except (OSError, ValueError) as exc:
            return _user_error(exc)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if getattr(args, "serve", False):
            # 只打印命令，不自行拉起浏览器/网络（设计 §3.4）
            print(f"allure serve {out}", file=sys.stderr)
        return 0
    if args.command == "allure-analyze":
        from .allure_analysis import analyze_allure, format_text
        # 知识库：用插件装配（与 review/knowledge 命令同源）
        knowledge = plugin.knowledge()
        to_tower = None
        if getattr(args, "to_tower", False):
            from .experience import ExperienceJournal
            to_tower = ExperienceJournal(config.reports_dir)
        try:
            result = analyze_allure(args.results_dir, knowledge=knowledge, to_tower=to_tower)
        except (OSError, ValueError) as exc:
            return _user_error(exc)
        if getattr(args, "format", "json") == "text":
            print(format_text(result))
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "report":
        from .reporting import export_runlog
        try:
            print(export_runlog(args.input, args.output))
        except (OSError, ValueError) as exc:
            return _user_error(exc)
        return 0
    if args.command == "recycle":
        return _recycle(args, config)
    if args.command == "review":
        return _review(args, config, plugin)
    if args.command == "explore":
        return _explore(args, config)
    if args.command == "overlay":
        return _overlay(args, config)
    if args.command == "gen":
        return _gen(args, config)
    if args.command == "review-code":
        return _review_code(args, config)
    if args.command == "doctor":
        legacy = config.legacy_project
        api = config.api_project
        # base_url 口径必须与 DemoRuntime 一致：Atlas .env 缺失时继承旧项目 .env，
        # 否则 doctor 报 false 但 atlas run 实际能连（诊断误导）。记录来源，不复制凭据。
        own_base_url = bool(config.base_url)
        load_dotenv(legacy)
        checks = {"python": sys.version.split()[0], "root": str(config.root), "env": config.env,
                  "env_prefix": config.env_url_key.rsplit("URL_", 1)[0],
                  "base_url_configured": own_base_url or bool(config.base_url),
                  "base_url_source": "atlas.env" if own_base_url else ("legacy.env" if config.base_url else "unset"),
                  "legacy_web": config.legacy_web,
                  "legacy_pages": (legacy / config.legacy_web / "lib/pages.py").is_file(),
                  "legacy_pytest_ini": (legacy / config.legacy_web / "pytest.ini").is_file(),
                  "demo_api": (api / "scripts" / config.api_module_file).is_file(),
                  "knowledge_pitfalls": len(plugin.knowledge().pitfalls())}
        # Docker/CI 资产一致性：防「已提供但已过期」——引用的文件/命令漂移时提前暴露
        docker_ok = True
        dockerfile = config.root / "Dockerfile"
        if dockerfile.exists():
            text = _read_text_safe(dockerfile)
            if text is None:
                checks["docker_unreadable"] = str(dockerfile)   # 非 UTF-8/权限：汇报而非崩溃整个诊断
                docker_ok = False
            else:
                for ref in ("pyproject.toml", "requirements.txt", "examples/smoke_browser.py"):
                    if ref in text and not (config.root / ref).exists():
                        checks["docker_ref_missing"] = ref
                        docker_ok = False
                checks["docker_cmd_matches_compose"] = _compose_cmd_matches(config.root)
                if not checks["docker_cmd_matches_compose"]:
                    docker_ok = False
        checks["docker_assets_consistent"] = docker_ok
        print(json.dumps(checks, ensure_ascii=False, indent=2))
        return 0 if checks["legacy_pages"] and checks["demo_api"] and docker_ok else 1
    if args.command == "legacy":
        try:
            adapter = LegacyAdapter(config)
        except (OSError, ValueError) as exc:
            return _user_error(exc)
        headed = getattr(args, "headed", False)
        if args.dry_run:
            # dry-run 的文档语义是「只检查路径和命令」——修复前对不存在的目录照样
            # 打印命令并返回 0，检查形同虚设；真实 CI 里会假绿通过预检。
            try:
                adapter.check_exec_dir()
            except (OSError, ValueError) as exc:
                return _user_error(exc)
            print(" ".join(adapter.pytest_command(args.node, headed, args.keyword)))
            print(f"cwd={adapter.exec_dir}")
            return 0
        try:
            return adapter.run_pytest(args.node, headed, args.keyword)
        except FileNotFoundError as exc:
            # 路径是输入问题（rc 2），不是执行失败；pytest 自身退出码原样透传不吞
            return _user_error(exc)
    if args.command == "compile":
        # 离线门禁：任务引用、assert 块、数据供给字段在 compile 期就报错，不登录不执行
        try:
            flow = FlowCompiler(TaskRegistry()).load(args.flow)
            tasks = FlowCompiler(_full_registry()).validate_flow(flow)
        except (OSError, ValueError, KeyError, RuntimeError, yaml.YAMLError) as exc:
            return _user_error(exc)
        print(json.dumps({"flow": str(args.flow), "tasks": [t.name for t in tasks],
                          "asserts": len(flow.get("assert") or [])}, ensure_ascii=False, indent=2))
        return 0
    if args.command == "run":
        if args.bu:
            from .plugins.demo.tasks import apply_bu
            try:
                result = apply_bu(args.bu, plugin.knowledge().load_overlays())
            except (KeyError, ValueError) as exc:
                # KeyError=BU 不在库；ValueError=#134 BU 值形态非映射（手滑存成数组/字符串）。
                # 旧只捕 KeyError，AttributeError 漏网变裸 traceback，违反用户输入 rc2 口径。
                print(str(exc), file=sys.stderr)
                return 2
            print(f"BU={result['bu']} 坐标生效：{json.dumps(result['applied'], ensure_ascii=False)}"
                  f"；未接执行路径/被显式env覆盖：{result['ignored']}", file=sys.stderr)
        # load 是「用户传入 flow 文件」的输入边界，与 compile 同口径：路径坏/YAML 非法/误传状态机
        # 文件都属输入错误 → 一行 stderr + rc 2，且在 DemoRuntime 构造/登录**之前**失败（fail fast，
        # 不启动浏览器不登录）。真正的执行失败（EnvBlocked/断言/网络）仍走下方 runtime.run 原样语义，
        # 绝不降级成输入错误。
        try:
            flow = FlowCompiler(TaskRegistry()).load(args.flow)
            # #97：结构校验（步骤形态/task 引用/assert/requires 供给块/flow 级策略）同为输入错误，
            # 旧实现登录+开浏览器之后才在 compile/provision 里炸——真实踩点：`atlas run` 跑坏 flow
            # 会先起真实 Chromium 走真实 SIT 登录再报裸 KeyError，浪费会话还可能留半截现场。
            # registry 与运行时同源（_full_registry ≡ DemoRuntime.registry），compile 绿=run 不再结构炸。
            FlowCompiler(_full_registry()).validate_flow(flow)
        except (OSError, ValueError, KeyError, RuntimeError, yaml.YAMLError) as exc:
            return _user_error(exc)
        # #103：入口输入错误（缺环境 URL / 坏 DemoApi 路径）与 DemoRuntime.__enter__ 同口径
        # 预检——先继承旧项目 .env 再检查（doctor 同款坑：不 load_dotenv 会用未继承的
        # ATLAS_DEMO_API_*/URL 误报缺失）。放在 with 之前：这些是输入错误（一行 stderr + rc 2），
        # 不得裸 traceback，更绝不允许走到开浏览器/真实登录（fail-fast 与 #97 同红线）。
        # 探针实锤：旧实现合法 flow + 缺 DEMO_URL_SIT 直接裸崩 ValueError traceback。
        try:
            load_dotenv(config.legacy_project)
            if not config.base_url:
                raise ValueError(f"缺少 {config.env} 对应的 {config.env_url_key} 配置")
            config.validate_base_url(config.base_url)
            check_demo_api(config)
            config.check_legacy_paths()   # #103 延伸：坏 legacy 路径也在浏览器/登录前 rc 2
        except (OSError, ValueError) as exc:
            return _user_error(exc)
        with DemoRuntime(config, login=not args.no_login, account=args.account, ou=args.ou,
                        division_code=args.division, language=args.language,
                        create_quota=args.create_quota) as runtime:
            FlowCompiler(runtime.registry(), runtime.provider).run(
                {"engine": config, **flow}, runtime.actor)
        return 0
    return 2


def _recycle(args, config) -> int:
    """列出自动创建的单据，供人工回收。不自动删除——删除是写操作需人审。"""
    import json as _json
    # 默认与 DemoRuntime 落盘点同源（config.reports_dir），不再拿 CWD 相对路径赌工作目录。
    path = args.input if args.input is not None else config.reports_dir / "recycle_list.jsonl"
    if not path.exists():
        print("[]")
        return 0
    records = []
    skipped = 0
    # #132：尾行腰斩坏字节时整读崩——一条写坏的历史记录让人连要回收的单都列不出，
    # 且违反本函数自己的残行计数口径（#71）与 CLI 友好报错纪律（#79/#80）。
    try:
        raw = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raw = path.read_text(encoding="utf-8", errors="replace")
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = _json.loads(line)
        except _json.JSONDecodeError:
            skipped += 1   # 残行：跳过而非顶掉整份清单，否则一个坏行让人连要回收的单都列不出
            continue
        if not isinstance(parsed, dict):
            skipped += 1
            continue
        records.append(parsed)
    if skipped:
        print(f"回收清单有 {skipped} 行无法解析（残行/非对象），已跳过", file=sys.stderr)
    if not args.cleanup:
        print(_json.dumps(records, ensure_ascii=False, indent=2))
        print(f"共 {len(records)} 条自动创建记录。如需删除，请加 --cleanup（非生产环境）",
              file=sys.stderr)
        return 0
    # cleanup 是写操作：生产禁止，非生产也需人审
    if config.is_production:
        print("生产环境禁止删除操作", file=sys.stderr)
        return 1
    print(f"--cleanup 需要业务插件提供删除实现。当前共 {len(records)} 条记录：", file=sys.stderr)
    for item in records:
        doc = item.get("document")
        doc = doc if isinstance(doc, dict) else {}
        print(f"  {item.get('entity')}: {doc.get('orderNo', doc.get('documentNo', '?'))}",
              file=sys.stderr)
    print("请人工确认后调用业务插件的删除接口。Atlas 不自动删除业务单据。", file=sys.stderr)
    return 0

def _review(args, config, plugin) -> int:
    """审核塔：一条命令看完、批掉、驳回；批准是唯一的知识写入口。"""
    from .review import ReviewQueue, evidence_card
    queue = ReviewQueue(config.reports_dir)
    if args.stats:
        print(json.dumps(queue.stats(), ensure_ascii=False, indent=2))
        return 0
    if args.approve:
        try:
            row = queue.resolve(args.approve)
            result = queue.approve(plugin.knowledge(), args.approve, remedy=args.remedy,
                                   component=args.component, element=args.element)
        except (KeyError, ValueError, PermissionError) as exc:
            # PermissionError=#135 只读库拒绝：未设 ATLAS_KNOWLEDGE_DIR 时批准不得写包内资产，
            # 友好点名配置项而非裸 traceback（rc2 口径 #79/#80；写路径无半成品状态——
            # 闸门在落盘前抛，塔与队列纹丝不动）。
            print(str(exc), file=sys.stderr)
            return 2
        target = result.get("id") or f"{result.get('component')}.{result.get('element')}"
        print(json.dumps({"approved": target, "kind": row.get("kind", "pitfall"),
                          "task": row.get("task")}, ensure_ascii=False))
        return 0
    if args.reject:
        try:
            row = queue.resolve(args.reject)
        except (KeyError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            return 2
        queue.reject(args.reject, args.reason)
        print(json.dumps({"rejected": row.get("id"), "task": row.get("task")},
                         ensure_ascii=False))
        return 0
    if args.reject_kind:
        try:
            count = queue.reject_kind(args.reject_kind, args.reason)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        print(json.dumps({"rejected_batch": args.reject_kind, "count": count},
                         ensure_ascii=False))
        return 0
    if args.template:
        rows = queue.template_rows(args.kind or "selector_candidate")
        args.template.parent.mkdir(parents=True, exist_ok=True)
        with args.template.open("w", encoding="utf-8") as stream:
            for row in rows:
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(json.dumps({"template": str(args.template), "rows": len(rows),
                          "next": "人填 component/element/selector 或 remedy 后 "
                                  "atlas review --apply " + str(args.template)},
                         ensure_ascii=False))
        return 0
    if args.apply:
        decisions, broken = [], []
        # #135（#112 读侧同防线第 8 处收口）：决策文件是**人可手改**的输入边界，旧
        # `read_text(encoding="utf-8")` 整读在腰斩多字节尾行抛 UnicodeDecodeError——
        # 探针实锤裸 traceback 顶掉 CLI rc2 口径，且**崩在逐行之前，一条合法决策都没落**
        # （半途崩：首行本该已批准的事实因读崩变成全批没发生，审核者无从知道塔动了没有）。
        # 先严格读保真、撞坏字节回落 replace：残行 JSON 必败自然落既有 malformed 跳过分支，
        # 截断前的合法行照常批准——与 reporting/experience/review/recycle 六方同纪律。
        try:
            raw = args.apply.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raw = args.apply.read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(raw.splitlines(), 1):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                broken.append({"id": f"line{lineno}", "error": "malformed JSON"})
                continue
            if not isinstance(obj, dict):
                # #109：合法 JSON 但非映射（人编辑把整行改成数组/null）——旧实现透传给
                # queue.apply 后 row.get 裸 AttributeError 顶掉整批（rc2 口径失效）。
                broken.append({"id": f"line{lineno}", "error": f"决策行必须是映射，实为 {type(obj).__name__}"})
                continue
            decisions.append(obj)
        try:
            result = queue.apply(plugin.knowledge(), decisions)
        except PermissionError as exc:
            # #135 只读库：未设 ATLAS_KNOWLEDGE_DIR 时批量批准的闸门在**首条落盘前**抛
            # （save_* 先于队列改写），零行写回、无半途状态——点名配置缺失 rc2 而非裸
            # traceback。判据仍只有 root=None 一处（save_* 闸门）；此处只兜传播路径，
            # 不在 CLI 复制第二份判据（#92 改一漏一）。
            print(str(exc), file=sys.stderr)
            return 2
        result["failed"] = broken + result["failed"]
        # 失败非零退出：批量里混着没填的行不能被当成全绿
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if not result["failed"] else 2
    pending = queue.pending(args.kind)
    if not pending:
        print("审核队列清空。" if not args.kind else
              f"没有 kind={args.kind} 的待审。", file=sys.stderr)
        return 0
    if args.group:
        from .review import grouped_view
        print(grouped_view(pending))
    else:
        for row in pending:
            print(evidence_card(row))
            print("-" * 70)
    scope = f"（kind={args.kind}）" if args.kind else ""
    print(f"共 {len(pending)} 条待审{scope}；"
          f"全塔分类：{json.dumps(queue.stats()['pending_by_kind'], ensure_ascii=False)}",
          file=sys.stderr)
    if not args.group:  # 聚类视图里每堆已带批量指引，不再重复
        print("批准：atlas review --approve <id> --remedy \"...\""
              "（选择器候选加 --component/--element）；"
              "驳回：atlas review --reject <id> --reason \"...\"", file=sys.stderr)
    return 0

def _overlay(args, config) -> int:
    """BU 坐标双轨：diff 是只读的；--to-tower 才落 pending_review，批准走 atlas review。"""
    from .overlay import diff_overlays, load_overlays, to_tower
    try:
        overlays = load_overlays(args.file)
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.to_tower:
        if not args.diff:
            print("--to-tower 需要 --diff BASE_BU OTHER_BU", file=sys.stderr)
            return 2
        from .experience import ExperienceJournal
        try:
            result = to_tower(ExperienceJournal(config.reports_dir), args.file,
                              args.diff[0], args.diff[1])
        except KeyError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        result["next"] = "atlas review --kind bu_overlay  # 人审批准：--approve <id> --remedy \"<目标BU真实值>\""
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.diff:
        try:
            diffs = diff_overlays(overlays, args.diff[0], args.diff[1])
        except KeyError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        print(json.dumps({"base": args.diff[0], "other": args.diff[1],
                          "diffs": diffs}, ensure_ascii=False, indent=2))
        return 0
    print(json.dumps({"bus": sorted(overlays),
                      "keys": {bu: sorted(v) for bu, v in overlays.items()}},
                     ensure_ascii=False, indent=2))
    return 0

def _explore(args, config) -> int:
    if args.compile:
        from .explore import compile_trace
        from .experience import ExperienceJournal
        journal = ExperienceJournal(config.reports_dir)
        summary = compile_trace(args.compile, journal)
        print(json.dumps({"trace": str(args.compile), **summary,
                          "next": "atlas review  # 草稿全部 pending_review，审后成资产"},
                         ensure_ascii=False, indent=2))
        return 0
    from .explore import start
    try:
        trace = start(config, account=args.account, url=args.url, trace=args.trace)
    except PermissionError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"trace={trace}\n下一步：atlas explore --compile {trace}", file=sys.stderr)
    return 0


def _gen(args, config) -> int:
    """atlas gen：接口文档→测试代码生成。只生成不执行（红线 #9）。

    退出码：0 成功 / 2 输入问题（文档不存在/格式无法识别/--strict 未决项）/
    3 结构自检失败（生成的 YAML validate_flow 不通过——生成器缺陷）。
    """
    import json as _json
    from .generator import GenOptions, generate
    from .generator.sources import SourceError
    from .generator.emit_flow import FlowEmitError
    from .generator.checklist import ChecklistError

    options = GenOptions(
        scenario=args.scenario, level=args.level, strict=args.strict,
        force=args.force, plugin=args.plugin, flow_id=args.flow_id,
        headers_file=args.headers_file, naming=args.naming,
        check_only=args.check_only, to_tower=args.to_tower,
        with_pytest=args.with_pytest, source_type=args.source_type)
    # --with-pytest 需 allure-pytest（红线 #4：新运行时依赖先问）
    if options.with_pytest:
        try:
            import allure  # noqa: F401
        except ImportError:
            print("--with-pytest 需要 allure-pytest（未安装）：pip install allure-pytest + allure CLI",
                  file=sys.stderr)
            return 2
    # 输出目录
    out_dir = args.out
    if out_dir is None and not options.check_only:
        # 默认 exports/gen/<flow_id>/；flow_id 先由文档名派生
        out_dir = Path("exports/gen") / Path(args.source).stem
    # --force 覆盖控制
    if out_dir is not None and out_dir.exists() and not options.force and not options.check_only:
        if any(out_dir.iterdir()):
            print(f"产物目录已存在且非空：{out_dir}（用 --force 覆盖）", file=sys.stderr)
            return 2
    try:
        result = generate(args.source, out_dir, options,
                          registry=_full_registry())
    except SourceError as exc:
        return _user_error(exc)
    except FlowEmitError as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 3
    except ChecklistError as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 3
    # 结构自检失败（rc 3）
    if not result.compile_ok:
        print(f"结构自检失败：{result.report.get('compile')}", file=sys.stderr)
        return 3
    # --strict：低置信度/占位符未接 env 一律 rc 2
    if options.strict:
        issues = []
        if result.low_confidence:
            issues.append(f"低置信度 {len(result.low_confidence)} 项")
        if result.placeholders:
            issues.append(f"占位符 {len(result.placeholders)} 个未接 env")
        if result.conflicts:
            issues.append(f"契约冲突 {len(result.conflicts)} 条")
        if issues:
            print(f"--strict 未决：{', '.join(issues)}", file=sys.stderr)
            return 2
    # --to-tower：登记 trace_step pending_review
    if options.to_tower:
        from .experience import ExperienceJournal
        from .generator.tower import to_tower
        journal = ExperienceJournal(config.reports_dir)
        for flow in result.flows:
            to_tower(journal, task=f"gen:{flow['id']}", flow_id=flow["id"],
                     evidence={"source": str(args.source),
                               "flow_id": flow["id"],
                               "scenario": result.scenario,
                               "compile_ok": result.compile_ok})
    print(_json.dumps(result.report, ensure_ascii=False, indent=2))
    return 0


def _review_code(args, config) -> int:
    """atlas review-code：测试代码质量评审。

    退出码：0 通过 / 1 门禁失败（有 critical 或低于 --fail-under）/ 2 用户错误。
    边界：只评审、不改代码、不写知识；--to-tower 只造 pending_review 草稿。
    """
    from .review_code import review_code, format_text, format_json

    mode = getattr(args, "review_mode", "static")

    try:
        result = review_code(
            args.paths,
            mode=mode,
            kind=getattr(args, "kind", "auto"),
            format=getattr(args, "format", "allure"),
            out=getattr(args, "out", None),
            config_path=getattr(args, "config", None),
            baseline=getattr(args, "baseline", None),
            fail_under=getattr(args, "fail_under", None),
            to_tower=getattr(args, "to_tower", False),
            reports_dir=config.reports_dir,
            root=config.root,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return _user_error(exc)

    # --to-tower：findings 进审核塔
    if getattr(args, "to_tower", False):
        from .review_code import findings_to_tower
        all_findings = []
        for fr in result["results"]:
            all_findings.extend(fr.get("findings", []))
        count = findings_to_tower(all_findings, config.reports_dir)
        result["tower_drafts"] = count

    # 输出
    fmt = getattr(args, "format", "allure")
    if fmt == "text":
        all_findings = []
        for fr in result["results"]:
            all_findings.extend(fr.get("findings", []))
        text = format_text({**result, "findings": all_findings})
        print(text)
    elif fmt == "json":
        print(format_json(result))
    else:  # allure
        print(json.dumps({
            "summary": result["summary"],
            "rc": result["rc"],
            "allure_out": result.get("_allure_out", ""),
            "files": [{"file": fr["file"], "score": fr["score"]["total"],
                        "grade": fr["score"]["grade"],
                        "findings": fr["score"]["finding_count"]}
                      for fr in result["results"]],
        }, ensure_ascii=False, indent=2))

    return result["rc"]


if __name__ == "__main__":
    raise SystemExit(main())
