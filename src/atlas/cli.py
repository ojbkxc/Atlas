"""命令行只暴露确定性动作；不向第三方 AI 自动上传项目数据。"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path
from . import __version__
from .composition import catalog, compose_for_goal
from .config import AtlasConfig, load_dotenv
from .dsl import FlowCompiler, TaskRegistry
from .legacy import LegacyAdapter
from .plugins.demo import DemoPlugin, core_tasks
from .runtime import DemoRuntime

def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="atlas", description="知识驱动的 UI/API 测试自动化平台")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--env", default=None, choices=["sit", "uat-eu", "uat-amer", "uat-hk", "prod-eu", "prod-amer", "prod-hk"])
    parser.add_argument("--mode", default=None, choices=["run", "debug", "heal"])
    parser.add_argument("--headed", action="store_true", help="有头模式，供 DSL 调试")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="检查依赖、配置与外部资产（不登录）")
    commands.add_parser("tasks", help="输出核心 Task 元数据")
    compose_parser = commands.add_parser("compose", help="验证核心 Task 依赖拼装")
    compose_parser.add_argument("--goal", default="document_no")
    flow = commands.add_parser("run", help="执行声明式 DSL（需已登录的 Actor，使用 Python 入口注入）")
    compile_cmd = commands.add_parser("compile", help="离线编译校验 flow YAML（不登录、不执行）")
    compile_cmd.add_argument("flow", type=Path)
    flow.add_argument("flow", type=Path)
    flow.add_argument("--account", default=None, help="缺省取业务插件 session 默认账号")
    flow.add_argument("--ou", default=None, help="缺省取业务插件 session 默认 OU")
    flow.add_argument("--language", default=None, help="UI 语言，缺省取业务插件默认（选择器优先英文）")
    flow.add_argument("--division", default=None, help="接口 divisionCode，缺省取业务插件默认")
    flow.add_argument("--no-login", action="store_true", help="仅使用已有 storage state；不自动登录")
    flow.add_argument("--create-quota", type=int, default=0, help="本次允许自动建单数，默认 0")
    legacy = commands.add_parser("legacy", help="原样执行 legacy-suite pytest 用例")
    legacy.add_argument("node", nargs="?", default="testcases/demo_sit_regression/test_demo_sit_regression.py")
    legacy.add_argument("--headed", action="store_true")
    legacy.add_argument("--keyword", default="", help="pytest -k 过滤，例如 test_tc7_ or test_tc8_")
    legacy.add_argument("--dry-run", action="store_true")
    sm = commands.add_parser("machine", help="校验状态机 YAML 并输出目标路径（不执行）")
    sm.add_argument("file", type=Path)
    sm.add_argument("--target", default="", help="目标状态；省略则只做结构校验")
    contracts_cmd = commands.add_parser("contracts", help="查看契约分级覆盖情况")
    contracts_cmd.add_argument("--validate", action="store_true", help="对已装配接口做一次只读校验")
    selectors = commands.add_parser("selectors", help="查询 Demo 选择器注册表")
    selectors.add_argument("component", nargs="?", default="")
    agent = commands.add_parser("agent", help="Agent 规划/风险评估（默认只规划不执行）")
    agent.add_argument("--goal", default="document_no", help="目标产出键，如 document_no")
    agent.add_argument("--execute", action="store_true", help="真正执行；需风险允许或加 --approve")
    agent.add_argument("--approve", action="store_true", help="人工批准超过自动阈值的高风险执行")
    agent.add_argument("--max-auto-risk", default="read_only",
                       choices=["read_only", "low", "medium", "high"])
    report = commands.add_parser("report", help="将 Atlas JSONL 转为 runlog 步骤格式")
    report.add_argument("input", type=Path)
    report.add_argument("output", type=Path)
    summary = commands.add_parser("summary", help="聚合运行记录：状态、失败类、最慢步骤")
    summary.add_argument("input", type=Path, nargs="?", default=Path("reports/runs.jsonl"))
    knowledge = commands.add_parser("knowledge", help="查询知识库")
    knowledge.add_argument("query", nargs="?", default="")
    recycle = commands.add_parser("recycle", help="列出本次运行自动创建的单据（不自动删除）")
    recycle.add_argument("--input", type=Path, default=Path("reports/recycle_list.jsonl"),
                         help="回收清单文件")
    recycle.add_argument("--cleanup", action="store_true",
                         help="尝试删除造的单（写操作，需人审且非生产）")
    return parser

def _force_utf8_stdout() -> None:
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
    DemoPlugin().register_tasks(registry)
    from .runtime import DemoApiOperation, LegacyStepTask
    from .tasks import AssertApi, CallEndpoint, UiAction
    registry.register("api_call", lambda **kwargs: CallEndpoint(**kwargs))
    registry.register("ui_action", lambda **kwargs: UiAction(**kwargs))
    registry.register("demo_api", lambda **kwargs: DemoApiOperation(**kwargs))
    registry.register("assert_api", lambda **kwargs: AssertApi(**kwargs))
    registry.register("legacy_step", lambda **kwargs: LegacyStepTask(**kwargs))
    return registry

def _compose_cmd_matches(root: Path) -> bool:
    """compose 与 Dockerfile 的 python 命令必须一致（python3 对 python3）。

    背景：Dockerfile 基于 debian 系时只有 python3；compose 若写 python，
    回退基础镜像后容器内直接报 command not found。
    """
    compose = root / "docker-compose.yml"
    dockerfile = root / "Dockerfile"
    if not compose.exists() or not dockerfile.exists():
        return True
    df_python = "python3" if "python3 -m pytest" in dockerfile.read_text(encoding="utf-8") else "python"
    text = compose.read_text(encoding="utf-8")
    for token in ("python -m pytest", "python3 -m pytest"):
        if token in text and not token.startswith(df_python):
            return False
    return True

def main(argv: list[str] | None = None) -> int:
    _force_utf8_stdout()
    args = _parser().parse_args(argv)
    overrides = {key: value for key, value in {"env": args.env, "mode": args.mode}.items() if value}
    if args.headed:
        overrides["headless"] = False
    config = AtlasConfig.from_root(args.root, **overrides)
    plugin = DemoPlugin()
    if args.command == "tasks":
        print(json.dumps(catalog(core_tasks()), ensure_ascii=False, indent=2))
        return 0
    if args.command == "compose":
        for task in compose_for_goal(core_tasks(), args.goal):
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
        agent = AtlasAgent(production=config.is_production,
                           max_auto_risk=RiskLevel(args.max_auto_risk),
                           journal=config.reports_dir / "agent_decisions.jsonl")
        tasks = core_tasks()
        if not args.execute:
            print(json.dumps(agent.propose(tasks, args.goal), ensure_ascii=False, indent=2))
            return 0
        with DemoRuntime(config) as runtime:
            try:
                decision = agent.run(tasks, args.goal, runtime.actor, approved=args.approve)
            except ApprovalRequired as exc:
                print(json.dumps({"approved": False, "reason": str(exc)}, ensure_ascii=False, indent=2),
                      file=sys.stderr)
                return 3
        print(json.dumps(decision.as_dict(), ensure_ascii=False, indent=2))
        return 0
    if args.command == "machine":
        from .state_machine import StateMachine
        machine = StateMachine.load(args.file)
        if args.target:
            print(json.dumps({"target": args.target, "tasks": machine.path(args.target)},
                             ensure_ascii=False, indent=2))
        else:
            print(json.dumps({"initial": machine.initial,
                              "states": sorted(machine.states),
                              "terminal": sorted(machine.terminal)}, ensure_ascii=False, indent=2))
        return 0
    if args.command == "contracts":
        from .contracts import ContractRegistry
        path = Path(DemoPlugin().knowledge_root) / "contracts/core.json"
        registry = ContractRegistry.load(path)
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
        print(json.dumps(summarize(args.input), ensure_ascii=False, indent=2))
        return 0
    if args.command == "report":
        from .reporting import export_runlog
        print(export_runlog(args.input, args.output))
        return 0
    if args.command == "recycle":
        return _recycle(args, config)
    if args.command == "doctor":
        legacy = config.external_project("ATLAS_LEGACY_PROJECT", "../legacy-suite")
        api = config.external_project("ATLAS_DEMO_API_PROJECT", "../legacy-api")
        # base_url 口径必须与 DemoRuntime 一致：Atlas .env 缺失时继承旧项目 .env，
        # 否则 doctor 报 false 但 atlas run 实际能连（诊断误导）。记录来源，不复制凭据。
        own_base_url = bool(config.base_url)
        load_dotenv(legacy)
        checks = {"python": sys.version.split()[0], "root": str(config.root), "env": config.env,
                  "base_url_configured": own_base_url or bool(config.base_url),
                  "base_url_source": "atlas.env" if own_base_url else ("legacy.env" if config.base_url else "unset"),
                  "legacy_pages": (legacy / "legacy-web/lib/pages.py").is_file(),
                  "legacy_pytest_ini": (legacy / "legacy-web/pytest.ini").is_file(),
                  "demo_api": (api / "scripts/demo_api.py").is_file(),
                  "knowledge_pitfalls": len(plugin.knowledge().pitfalls())}
        # Docker/CI 资产一致性：防「已提供但已过期」——引用的文件/命令漂移时提前暴露
        docker_ok = True
        dockerfile = config.root / "Dockerfile"
        if dockerfile.exists():
            text = dockerfile.read_text(encoding="utf-8")
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
        adapter = LegacyAdapter(config)
        if args.dry_run:
            print(" ".join(adapter.pytest_command(args.node, args.headed, args.keyword)))
            print(f"cwd={adapter.root / 'legacy-web'}")
            return 0
        return adapter.run_pytest(args.node, args.headed, args.keyword)
    if args.command == "compile":
        # 离线门禁：任务引用、assert 块、数据供给字段在 compile 期就报错，不登录不执行
        flow = FlowCompiler(TaskRegistry()).load(args.flow)
        tasks = FlowCompiler(_full_registry()).compile(flow)
        print(json.dumps({"flow": str(args.flow), "tasks": [t.name for t in tasks],
                          "asserts": len(flow.get("assert") or [])}, ensure_ascii=False, indent=2))
        return 0
    if args.command == "run":
        flow = FlowCompiler(TaskRegistry()).load(args.flow)
        with DemoRuntime(config, login=not args.no_login, account=args.account, ou=args.ou,
                        division_code=args.division, language=args.language,
                        create_quota=args.create_quota) as runtime:
            FlowCompiler(runtime.registry(), runtime.provider).run(
                {"engine": config, **flow}, runtime.actor)
        return 0
    return 2

if __name__ == "__main__":
    raise SystemExit(main())


def _recycle(args, config) -> int:
    """列出自动创建的单据，供人工回收。不自动删除——删除是写操作需人审。"""
    import json as _json
    path = args.input
    if not path.exists():
        print("[]")
        return 0
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(_json.loads(line))
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
        doc = item.get("document", {})
        print(f"  {item.get('entity')}: {doc.get('orderNo', doc.get('documentNo', '?'))}",
              file=sys.stderr)
    print("请人工确认后调用业务插件的删除接口。Atlas 不自动删除业务单据。", file=sys.stderr)
    return 0
