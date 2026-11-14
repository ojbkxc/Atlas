"""命令行只暴露确定性动作；不向第三方 AI 自动上传项目数据。"""
from __future__ import annotations
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import yaml
from . import __version__, boundaries, versioning
from .composition import catalog, compose_for_goal
from .config import ENV_URL_KEYS, AtlasConfig, load_dotenv
from .dsl import FlowCompiler, FlowDocument, TaskRegistry
from .legacy import LegacyAdapter
from .plugin_api import resolve_plugin
from .runtime import DemoRuntime, check_demo_api, require_account, resolve_account

def core_tasks(legacy_pages_path=None):
    """任务枚举的插件缝**单一入口**（cli 的 tasks/compose/agent 经此走 resolve_plugin().tasks()）。

    缺省（无 ATLAS_PLUGIN）→ resolve_plugin() 返回 DemoPlugin，其 tasks() 即 demo 的
    core_tasks——与旧 `from .plugins.demo import core_tasks` 逐字节等价（同一函数、同一默认
    参数、同一返回）；ATLAS_PLUGIN=iservice → 列 iservice 的任务（换业务只换插件）。
    保留 `core_tasks` 之名以兼容既有测试对 `cli.core_tasks` 的替身注入（agent 轨 rc 终态测）。
    不在此写第二套分支判断（#92）：判据只在插件协议的 tasks()。
    """
    return resolve_plugin().tasks(legacy_pages_path)

def _parser() -> argparse.ArgumentParser:
    # 全局参数用 parents 共享：`atlas run x.yaml --mode debug`（文档口径）与
    # `atlas --mode debug run x.yaml` 都必须可用——真机冒烟抓到只认前者会 exit 2。
    global_args = argparse.ArgumentParser(add_help=False)
    # default=SUPPRESS：否则子解析器的默认值会覆盖全局位已设值（argparse 经典坑）。
    # 缺省语义由 main() 的 getattr 兜底。
    global_args.add_argument("--root", type=Path, default=argparse.SUPPRESS)
    # choices 从引擎单一真相派生（config.ENV_URL_SUFFIXES 的键，即环境集合的权威声明）；
    # 旧实现手抄了一份字面量列表——这是同一份环境知识的**第三份副本**（config 与
    # plugins/*/knowledge/envs.json 之外），#92 改一漏一：新增环境时 `config.py` 与插件
    # 的漂移测都会绿，只有 CLI 静默不认那个 `--env`，用户在命令行撞 "invalid choice"
    # 却说不清该改哪。派生后漂移由 tests/test_config.py 的守卫当场钉红。
    global_args.add_argument("--env", default=argparse.SUPPRESS, choices=list(ENV_URL_KEYS))
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
                             help="显式指定 Atlas 源码根（默认自动探测当前源码根并钉成 file:// 依赖）")
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
    export_pytest_cmd = add("export-pytest",
                            help="已存在的 flow.yaml → pytest 回归代码包（gap9b；只生成不执行，红线 #9）")
    export_pytest_cmd.add_argument("flow", type=Path, help="flow YAML 路径")
    export_pytest_cmd.add_argument("-o", "--out", type=Path, default=None,
                                   help="产物目录（默认 exports/pytest/<flow 文件名>/）")
    export_pytest_cmd.add_argument("--fast", action="store_true",
                                   help="快模式包（单浏览器/语义等待/runs.jsonl；UI 步需声明位，"
                                        "形态判据与 atlas compile 同源，红线 #9 只生成不执行）")
    export_pytest_cmd.add_argument("--force", action="store_true",
                                   help="产物目录非空仍写出（emit_pytest_package 现闸语义）")
    export_pytest_cmd.add_argument("--compat", default="none",
                                   choices=["none", "legacy-suite"],
                                   help="兼容输出形态：--compat legacy-suite 产出旧仓可直接运行的快包"
                                        "（不 import atlas，gap11）；必须与 --fast 同用，"
                                        "none=现行形态")
    export_pytest_cmd.add_argument("--parallel", type=int, default=0,
                                   help="并行分片 worker 数（T3；0/1=单进程默认）。>1 须配 --fast，"
                                        "产物 README 记并行用法；运行需 pytest-xdist，"
                                        "缺依赖运行期点名报错不静默降级")
    flow.add_argument("flow", type=Path)
    flow.add_argument("--account", default=None, help="缺省取业务插件 session 默认账号")
    flow.add_argument("--ou", default=None, help="缺省取业务插件 session 默认 OU")
    flow.add_argument("--language", default=None, help="UI 语言，缺省取业务插件默认（选择器优先英文）")
    flow.add_argument("--division", default=None, help="接口 divisionCode，缺省取业务插件默认")
    flow.add_argument("--no-login", action="store_true", help="仅使用已有 storage state；不自动登录")
    flow.add_argument("--create-quota", type=int, default=0, help="本次允许自动建单数，默认 0")
    flow.add_argument("--bu", default="", help="应用已批准 BU overlay 坐标到会话默认值（ATLAS_SESSION_* env 优先）")
    flow.add_argument("--case-timeout", type=int, default=None, metavar="SECONDS",
                      help="单条用例（flow）整体超时秒数，缺省 None=不限（保持兼容）。"
                           "到点强杀执行子进程（含 Chromium/驱动整棵进程树）、runs.jsonl 补记 "
                           "timeout 终态、返回 124（对齐外部 timeout 语义；不是 failed）")
    flow.add_argument("--harvest", action="store_true",
                      help="跑绿后把动作序列固化为 flow_candidate 草稿进审核塔"
                           "（落库线；批准后落公共任务库，等价 flow 级 harvest: true）")
    legacy = add("legacy", help="原样执行 legacy-suite pytest 用例")
    legacy.add_argument("node", nargs="?", default=None,
                        help="pytest 节点；缺省取 ATLAS_LEGACY_TEST_NODE 或内置示例")
    # --headed 由 global_args 统一提供（子命令前后均可），不再局部重复定义
    legacy.add_argument("--keyword", default="", help="pytest -k 过滤，例如 test_tc7_ or test_tc8_")
    legacy.add_argument("--fast", action="store_true",
                        help="注入 atlas.fastplugin 快垫片并把 PYTHONPATH 相对项转绝对（gap11；"
                             "可与 --headed/--keyword/node 自由组合；不给时行为逐字节不变）")
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
    allure_cmd.add_argument("--clean", action=argparse.BooleanOptionalAction, default=True,
                            help="先清空结果集里 Atlas 前缀文件（默认即清；--no-clean 可关闭；"
                                 "仅删 atlas-* 不误删用户附件）")
    allure_cmd.add_argument("--serve", action="store_true",
                            help="生成后打印 allure serve 命令（不自行拉起浏览器/网络）")
    allure_cmd.add_argument("--source-dimension", action="store_true",
                            help="historyId 掺来源维度（run 记录的 plugin 键）——多平台/并发 run "
                                 "不再合并成一条趋势线（cc-6）。口径变更会把存量历史断线，"
                                 "故默认关（不带本开关 = 逐字旧口径 sha1(env|case)）")
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
    library = add("library", help="公共任务库：已审核的跨流程可复用动作序列（只读查询；"
                                  "写路径只有 review --approve）")
    library.add_argument("name", nargs="?", default="",
                         help="条目名（省略则列出全部）")
    recycle = add("recycle", help="列出本次运行自动创建的单据（不自动删除）")
    recycle.add_argument("--input", type=Path, default=None,
                         help="回收清单文件（默认 root 下 reports/recycle_list.jsonl，与 run 落盘同源）")
    recycle.add_argument("--cleanup", action="store_true",
                         help="尝试删除造的单（写操作，需人审且非生产）")
    review = add("review", help="审核吞吐入口：草稿队列→证据卡片→批准/驳回→留痕")
    review.add_argument("--stats", action="store_true", help="队列与吞吐统计")
    review.add_argument("--approve", default=None, metavar="ID",
                        help="按 id/前缀批准：坑点进知识，选择器候选进组件库（按 kind 分发）")
    review.add_argument("--remedy", default="", help="批准时的修复动作（坑点）或选择器本身（候选）："
                        "CSS 字符串，或结构化定位描述 '{\"role\":\"button\",\"name\":\"保存\"}'")
    review.add_argument("--role", default="", help="批准 selector_candidate：ARIA 角色（配 --name 直接入库，"
                        "不必手抄成 CSS）")
    review.add_argument("--name", default="", help="批准 selector_candidate：ARIA 无障碍名（配 --role）")
    review.add_argument("--component", default="", help="批准 selector_candidate：写入的组件名")
    review.add_argument("--element", default="", help="批准 selector_candidate：写入的元素名")
    review.add_argument("--reject", default=None, metavar="ID", help="按 id/前缀驳回")
    review.add_argument("--reject-kind", default="",
                        choices=["", "pitfall", "selector_candidate", "trace_step", "bu_overlay",
                                 "flow_candidate"],
                        help="整类驳回（原因必填），批量清塔")
    review.add_argument("--template", type=Path, default=None, metavar="FILE",
                        help="导出人可编辑的决策模板 JSONL（配合 --kind 选类）")
    review.add_argument("--apply", type=Path, default=None, metavar="FILE",
                        help="执行填好的决策模板：逐条批准分发留痕，单行失败不拦其余")
    review.add_argument("--reason", default="", help="驳回原因；配 --allow-unverified 批准时必填（留痕依据）")
    review.add_argument("--allow-unverified", action="store_true",
                        help="证据三元组缺失时人工显式豁免批准（C2 override 通道，必须配 --reason；"
                             "只在 --require-evidence 生效时才有意义）")
    review.add_argument("--require-evidence", action="store_true",
                        help="启用 C2 证据晋级闸：可批准 kind 缺 evidence.verification 四元组时拒绝"
                             "批准（默认**不**强制——重构波把该闸降级为显式 opt-in；批量的 --apply 同受此开关约束）")
    review.add_argument("--kind", default="",
                        choices=["", "pitfall", "selector_candidate", "trace_step", "bu_overlay",
                                 "flow_candidate"],
                        help="只看某一类待审草稿")
    review.add_argument("--group", action="store_true",
                        help="聚类视图：批量类按证据源压成紧凑表，坑点保留全卡片")
    explore = add("explore", help="开 CDP 探索会话（非生产）供外部大脑/playwright-cli attach；或编译 trace 为草稿")
    explore.add_argument("--url", default=None, help="探索起点，缺省为登录后的页面")
    explore.add_argument("--account", default=None, help="探索账号（建议只读角色）")
    explore.add_argument("--trace", type=Path, default=None, help="trace 输出路径")
    explore.add_argument("--compile", type=Path, default=None, metavar="TRACE",
                         help="把 trace.jsonl 编译成 pending_review 草稿（不开浏览器）")
    explore.add_argument("--auto", action="store_true",
                         help="LLM 自主探索（gap12）：目标进去、草稿出来，全程无人；须配 --goal 与 "
                              "ATLAS_LLM_BASE_URL/_API_KEY/_MODEL；写类动作默认被审批门拦停")
    explore.add_argument("--goal", default=None, help="--auto 的探索目标（自然语言）")
    explore.add_argument("--max-steps", type=int, default=30, help="--auto 步数上限（默认 30）")
    explore.add_argument("--max-seconds", type=float, default=600.0, help="--auto 墙钟上限秒（默认 600）")
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
                            help="额外产出可独立运行的 pytest 回归代码包（自包含，仅需 pytest）")
    gen_parser.add_argument("--fast", action="store_true", default=False,
                            help="快模式产物：单浏览器/单登录+语义等待+零成功截图+runs.jsonl（须配 --with-pytest，红线 #9 只生成不执行）")
    devops_sync_parser = add("devops-sync",
                             help="同步 Allure 结果集到美的 DevOps 平台（用例库/测试计划；默认 dry-run 不联网）")
    devops_sync_parser.add_argument("--suite", default=None,
                                    help="只同步指定 suite（allure-results 含多 suite 时建议指定）")
    devops_sync_parser.add_argument("--execute", action="store_true",
                                    help="真实调用 MCP 上传（默认只算不传；对外平台同步属敏感操作）")
    devops_sync_parser.add_argument("--config", type=Path, default=None,
                                    help="devops-sync.yaml 路径（默认 <root>/devops-sync.yaml）")
    devops_sync_parser.add_argument("--results-dir", type=Path, default=None,
                                    help="Allure 结果集目录（默认 <root>/reports/allure-results）")
    devops_sync_parser.add_argument("--mcp-server", default=None,
                                    help="mcp.json 中的服务器名（如 devops-mcp-sit/uat/prd；默认自动解析）")
    devops_sync_parser.add_argument("--format", default="json", choices=["json", "text"],
                                    help="输出格式（默认 json）")
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
    schedule = add("schedule",
                   help="调度核（C1）：由 OS cron / Windows 任务计划器调起的一次性命令；"
                        "Atlas 不做常驻进程，也不代注册 OS 任务（crontab 子命令只出可粘贴的成品行）")
    schedule_sub = schedule.add_subparsers(dest="schedule_command", required=True)

    def schedule_add(name: str, **kw):
        return schedule_sub.add_parser(name, parents=[global_args], **kw)

    sched_add = schedule_add("add", help="登记任务（默认只读档：含写步骤的 flow 必须显式 --i-accept-writes）")
    sched_add.add_argument("id")
    sched_add.add_argument("--flow", type=Path, required=True, help="被派发的 flow（存 root 相对 POSIX 串）")
    sched_add.add_argument("--cron", default=None, metavar="EXPR", help="5 段 cron 表达式（自写解析器）")
    sched_add.add_argument("--tz", default=None, metavar="NAME", help="IANA 时区名；cron 必填")
    sched_add.add_argument("--at", default=None, metavar="ISO", help="once 任务的一次性时刻（ISO 8601）")
    sched_add.add_argument("--manual", action="store_true", help="手动档：不参与自动补跑判定")
    sched_add.add_argument("--run-arg", dest="run_args", action="append", default=[], metavar="ARG",
                           help="透传给 atlas run 的 argv（只准账号名等非凭据值；--approve/--mode 当场拒）")
    sched_add.add_argument("--max-duration-min", type=int, default=None,
                           help="单次派发上限分钟（到点 abort，缺省 30）")
    sched_add.add_argument("--i-accept-writes", dest="accept_writes", action="store_true",
                           help="确认无人值守跑含写步骤的 flow（人的决定记录，红线 #13）")
    schedule_add("list", help="台账一览")
    sched_status = schedule_add("status", help="错过几轮 / 连败 / AUTO-PAUSED 汇总（G2 交付答案）")
    sched_status.add_argument("id", nargs="?", default=None)
    for name, hint in (("enable", "重新启用（当场重置水位线与连败：改配置的瞬间不回放历史）"),
                       ("disable", "停用（保留水位线，恢复用 enable）"),
                       ("remove", "从台账删除（调度索引保留历史）")):
        cmd = schedule_add(name, help=hint)
        cmd.add_argument("id")
    sched_edit = schedule_add("edit", help="改配置（改 cron/tz/flow/run_args 任一 → 重置水位线+连败+原因）")
    sched_edit.add_argument("id")
    sched_edit.add_argument("--flow", type=Path, default=None)
    sched_edit.add_argument("--cron", default=None, metavar="EXPR")
    sched_edit.add_argument("--tz", default=None, metavar="NAME")
    sched_edit.add_argument("--at", default=None, metavar="ISO")
    sched_edit.add_argument("--manual", action="store_true")
    sched_edit.add_argument("--run-arg", dest="run_args", action="append", default=None, metavar="ARG",
                            help="整组替换（不给 = 不动）")
    sched_edit.add_argument("--max-duration-min", type=int, default=None)
    sched_edit.add_argument("--i-accept-writes", dest="accept_writes", action="store_true")
    sched_run = schedule_add("run", help="跑一轮 tick（给 OS 调度器调起；含租约互斥、补跑、连败暂停）")
    sched_run.add_argument("id")
    sched_run.add_argument("--dry-run", action="store_true",
                           help="只体检+决策并打印将要执行的命令，不抢租约、不写台账、不派发")
    sched_cron = schedule_add("crontab", help="打印接线命令（Linux crontab 行 / Windows schtasks 行；绝不代跑）")
    sched_cron.add_argument("id")
    sched_cron.add_argument("--platform", default="auto", choices=["auto", "linux", "windows"],
                            help="接线形态（auto 按 sys.platform；终验 X8 要求两形态都有产出）")
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
        # 未探测到 Atlas 源码根＝骨架的 `pip install -e .` 可能解析不到 atlas-test-platform
        # （Atlas 只以源码分发）。如实 WARN + 给出两条补救路径，不静默给一个装不上的骨架。
        if (out.get("atlas_dependency") or {}).get("mode") == "index":
            print("WARN: 未探测到 Atlas 源码根，生成项目的 pyproject 按索引名依赖 "
                  "atlas-test-platform；该名若不可解析，请先 `pip install -e <Atlas 仓库>`，"
                  "或用 `atlas init --atlas-path <Atlas 仓库>` 重新生成（钉成 file:// 依赖）。",
                  file=sys.stderr)
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
    if args.command == "library":
        return _library(args, plugin)
    if args.command == "agent":
        from .agent import ApprovalRequired, AtlasAgent, RiskLevel
        from .failures import (AwaitingHuman, DeliveryUncertain, FlowCompositionError,
                               exception_brief)
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
            # 空账号也在 `with` 之前投递成 rc 2（判据/消息在 runtime.require_account 单点）：
            # 旧实现只在 __enter__ 判，ValueError 穿透 main() 变 ~20 行 traceback。
            # agent 子命令无 --account（账号只来自插件/知识库坐标），故 getattr 兜底。
            require_account(config, resolve_account(config, getattr(args, "account", None)))
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
            except AwaitingHuman as exc:
                # C9 补齐（2026-11-06）：agent 轨此前只捕审批门与组合错，`AwaitingHuman`
                # 一路穿透到顶层泛捕获，journal 记「执行失败」、CLI 返 rc1——run 轨早就有的
                # 「等人=rc3」显态在 agent 轨是漏网第二条腿（#92 改一漏一的又一处）。
                # rc3 与本分支既有 ApprovalRequired 同值：两者都是「需要人来处置」，
                # 口径仍限 run/agent/dispatch 子进程面（评审必改 C9-D）。
                # 差异如实标注：agent 轨不经 dsl/RunRecorder，**不产 runs.jsonl 的 waiting 行**，
                # 终态只体现在 rc + stderr + journal reason（journal 侧见 agent.run 的 outcome 分流）。
                print(f"WAITING-HUMAN: {exception_brief(exc)[:200]}", file=sys.stderr)
                return 3
            except DeliveryUncertain as exc:
                # C12 补齐：写可能已发出、结果未知。rc 仍是 1（真失败，红线 #7 不洗绿），
                # 但终端必须与一般失败区分开——看到「执行失败」的直觉动作是重跑，
                # 而这里的正确动作是先核实系统里有没有这条数据（重跑=写双跑）。
                print(f"DELIVERY-UNCERTAIN: {exception_brief(exc)[:200]}", file=sys.stderr)
                return 1
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
        from .reporting import run_attention, summarize
        from .review import ReviewQueue
        try:
            result = summarize(args.input)
        except (OSError, ValueError) as exc:
            return _user_error(exc)
        # C9：「等人」与操作/环境族计数单独成块——批量巡检的失败列表里混着等人工步骤时，
        # 处置动作是找人而不是查 bug。塔读数**真去读**再传进去：没读就没资格说 0（#64），
        # 而这里读了，所以 notes 里的 TOWER-NOT-READ 只在调用方不读塔时出现（单元测钉）。
        # summarize 本体零改动：status Counter 天然容纳新终态取值（#115），attention 是
        # 附加键，既有 status/fail_classes/versions 各键的取值形态逐字节不变。
        result["attention"] = run_attention(result, ReviewQueue(config.reports_dir).stats())
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "allure":
        from .allure_report import ALLURE_RESULTS_DIR, emit_allure
        # 默认输出与 config.reports_dir 同源（不赌 CWD）；输入缺省 <root>/reports/runs.jsonl
        runs = args.input if args.input is not None else config.reports_dir / "runs.jsonl"
        out = args.out if args.out is not None else config.reports_dir / ALLURE_RESULTS_DIR
        try:
            # #139：相对附件按项目根解析（engine 落 runs.jsonl 的就是 root 相对路径）；
            # 旧实现不传，消费侧回落 runs_path.parent=reports/ 做基准——截图明明在
            # root/artifacts/snapshots/ 却假报「无法读取」生成占位（假阴性证据）。
            # clean 由 --clean/--no-clean 控制（默认即清，与历史行为逐字节一致）
            result = emit_allure(runs, out, clean=args.clean, attachments_dir=config.root,
                                 source_dimension=getattr(args, "source_dimension", False))
        except (OSError, ValueError) as exc:
            return _user_error(exc)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if getattr(args, "serve", False):
            # 只打印命令，不自行拉起浏览器/网络（设计 §3.4）
            print(f"allure serve {out}", file=sys.stderr)
            # C4③ 分发口径落文案：结果集里可能有真实 URL/单号/截图，自建 HTTP 只绑回环
            # （既有测试断言的是上一行的 `allure serve <目录>` 子串，本行只做补充提示）
            print("分发提醒：如需自建 HTTP 服务，只绑回环——"
                  "python -m http.server 8765 --bind 127.0.0.1 --directory <报告目录>",
                  file=sys.stderr)
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
    if args.command == "devops-sync":
        return _devops_sync(args, config)
    if args.command == "review-code":
        return _review_code(args, config)
    if args.command == "schedule":
        return _schedule(args, config)
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
                  "knowledge_pitfalls": len(plugin.knowledge().pitfalls()),
                  # C6 版本三元组入诊断：`atlas doctor` 是「本机现状」的快照入口，
                  # 三元组在这里能当场看出「知识被人手改过（source=private，sha1 变了）」
                  # 或「根本没配私有目录（source=builtin，读的是仓库虚构默认）」。
                  # 展示项，不改 rc 逻辑；unreadable 指纹如实报 source，不静默。
                  "version_triple": {
                      "atlas": __version__,
                      "knowledge": versioning.knowledge_fingerprint(
                          getattr(plugin.knowledge(), "root", None)),
                      "tz_data": versioning.tz_data_status()}}
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
        # C5 边界账本一致性（design_c5 §4）：账本是数据不是引擎字面量（红线 #11）——
        # 引擎运行时永不解析 docs/ownership/，只有这里与 scripts/boundary_classify.py
        # 两个薄消费方，判据唯一实现在 atlas.boundaries（#92，doctor 不复制第二份校验）。
        # rc 并入 docker_ok 同侧：账本「存在但坏」=漂移 → rc1（与 docker 资产同纪律 #134）；
        # 文件缺失 → present:false **不改 rc**——新波次前的历史分支/精简克隆不该被账本卡死，
        # 如实报缺失不猜不编（缺账本 ≠ 账本漂移，两个事实分开陈述）。
        bpath = boundaries.ledger_path(config.root)
        ledger_present = bpath.is_file()
        boundary_ok = True
        if ledger_present:
            ledger, ledger_problems = boundaries.load_ledger(bpath)
            ledger_warnings, ledger_uncovered = boundaries.ledger_disk_coverage(config.root, ledger)
            boundary_ok = (not ledger_problems) and (not ledger_uncovered)
            regions = ledger.get("regions")
            checks["boundary_ledger"] = {
                "present": True,
                "regions": len(regions) if isinstance(regions, list) else 0,
                "problems": ledger_problems,
                "warnings": ledger_warnings,
                "uncovered_regions": ledger_uncovered,
                "accepted_debts": boundaries.accepted_debt_summary(ledger)}
            checks["boundary_assets_consistent"] = boundary_ok
        else:
            checks["boundary_ledger"] = {"present": False, "path": boundaries.LEDGER_REL_PATH}
        print(json.dumps(checks, ensure_ascii=False, indent=2))
        return 0 if checks["legacy_pages"] and checks["demo_api"] and docker_ok and boundary_ok else 1
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
            fast = getattr(args, "fast", False)
            print(" ".join(adapter.pytest_command(args.node, headed, args.keyword,
                                                  fast=fast)))
            print(f"cwd={adapter.exec_dir}")
            if fast:
                # 可核性：注入后的 PYTHONPATH（相对项已转绝对）+ 开关清单**三态**——
                # (explicit)=用户显式设（含 off，恒优先）；
                # (default --fast)=--fast 置位的**清单内**键（#36 后只剩截图抑制）；
                # unset=未开。旧版只看父 os.environ 会把「默认已开」显示成 unset——
                # 展示的必须是子进程实际会拿到的 env。
                env = adapter.fast_child_env()
                print(f"PYTHONPATH={env.get('PYTHONPATH', '')}")
                for key in adapter.FAST_ENV_SWITCHES:
                    explicit = os.environ.get(key)
                    if explicit:
                        print(f"{key}={explicit} (explicit)")
                    elif env.get(key):
                        print(f"{key}={env[key]} (default --fast)")
                    else:
                        print(f"{key}=unset")
            return 0
        try:
            return adapter.run_pytest(args.node, headed, args.keyword,
                                      fast=getattr(args, "fast", False))
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
    if args.command == "export-pytest":
        return _export_pytest(args)
    if args.command == "run":
        # --case-timeout 是参数值校验（输入错误 rc 2 口径），排在 flow load 之前：
        # 值本身非法时没必要先读文件再报第二个错。
        case_timeout = getattr(args, "case_timeout", None)
        if case_timeout is not None and case_timeout <= 0:
            return _user_error(ValueError(
                f"--case-timeout 必须是正整数秒（不限就别传），收到 {case_timeout}"))
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
            # 空账号也在 `with` 之前投递成 rc 2（判据/消息在 runtime.require_account 单点）：
            # 旧实现只在 __enter__ 判，ValueError 穿透 main() 变 ~20 行 traceback。
            # --no-login（不登录）时不查：没有会话就没有「拿空账号去登录」这件事。
            if not args.no_login:
                require_account(config, resolve_account(config, args.account))
        except (OSError, ValueError) as exc:
            return _user_error(exc)
        if case_timeout is not None:
            # per-case 超时（执行层短板 #1）：supervisor 在父进程看护，真实执行在子进程。
            return _run_case_supervised(args, config, flow, case_timeout)
        from .failures import AwaitingHuman, exception_brief
        # 落库线：--harvest 注入 flow 级开关（overlay 保形态；单次运行的显式人决）。
        # 注入必须在这里而非 run() 内部——dsl.run 只认 flow dict 里的键。
        if getattr(args, "harvest", False):
            flow = flow.overlay(harvest=True) if isinstance(flow, FlowDocument) \
                else {"harvest": True, **flow}
        try:
            with DemoRuntime(config, login=not args.no_login, account=args.account, ou=args.ou,
                             division_code=args.division, language=args.language,
                             create_quota=args.create_quota) as runtime:
                # overlay 而不是 {**flow}：字典解包会丢掉 FlowDocument 的源文本形态，
                # md5 编译缓存与 ^ 指针定位随之失效（同一份 flow 白编译第二遍、报错白丢行号）
                FlowCompiler(runtime.registry(), runtime.provider).run(
                    flow.overlay(engine=config) if isinstance(flow, FlowDocument)
                    else {"engine": config, **flow}, runtime.actor)
        except AwaitingHuman as exc:
            # C9 显态：**等人不是失败**——run 轨 rc 3 是「需要人来处置」的信号，
            # 与 agent 审批门的 rc 3 同值（cli agent 分支现盘 :454-457 已实测；口径限定在
            # run/agent/dispatch 子进程面，`atlas gen` 的 rc 3 是结构自检失败，两义不并表——
            # 评审必改 C9-D）。0/1/2/124/130 既有语义零变化：1 仍是真实失败，2 仍是输入错误。
            # 收尾照常：`with` 的 __exit__ 在 except 之前跑完（浏览器关闭、回收清单落盘、
            # 决策留痕都不因这条腿丢失），runs.jsonl 的 waiting 行已由 dsl 落好。
            # stderr 前缀 ASCII（X4：`WAITING-HUMAN:`），冒号后才可能是中文——中文段走
            # exception_brief 的净化刀（C4②），截 200 防长报文淹没终端。
            print(f"WAITING-HUMAN: {exception_brief(exc)[:200]}", file=sys.stderr)
            return 3
        return 0
    return 2


# ── per-case 超时（执行层短板 #1）────────────────────────────────────────────
# 选型：**子进程执行单 case**（父进程 supervisor 看护），不用「线程看门狗 + 强制
# page.close」。理由：
# 1. Playwright sync API 对象与创建它的 greenlet/线程绑定，另一线程调 page.close()
#    会直接抛 greenlet 错误——线程看门狗救不了场；Python 也无法从外部中断主线程里
#    阻塞中的调用（Windows 没有 SIGALRM，signal 只能在主线程收）。
# 2. 2026-11-05 批量实跑的卡死形态（567s Page crashed）挂在驱动层：in-process 的
#    page.close 自己也要走同一条已挂死的驱动连接，无法自救。
# 3. 子进程方案与此前外部 `timeout 600 atlas run ...` 的语义一致（要替代的正是它），
#    且更强：外部 timeout 杀完进程 runs.jsonl 什么都不剩，这里由父进程补记 timeout
#    终态行（超时不是 failed，不污染 pass/fail 缺陷分布）。
# 4. 「中断该 case、继续下一条」由批量层的既有形态兑现：每条用例就是一次 `atlas run`，
#    本命令确定性地在 timeout 秒内结束（rc 124），外层循环自然继续下一条。
# 父进程已完成全部输入预检（flow 结构/base_url/DemoApi/legacy 路径），子进程重跑同一
# 条 `atlas run`（argv 不带 --case-timeout，杜绝递归），这些幂等预检在子进程里再过一遍
# 的开销是毫秒级 is_file/check，换来自包含与常规路径完全一致的执行语义。

def _case_timeout_child_argv(args, root: Path) -> list[str]:
    """派发子进程的 argv（X2 纪律：全列表不经 shell）。显式重建而**不是**复制 sys.argv：
    --case-timeout 必须被剔除（否则子进程再派孙进程，超时层无限递归），其余 run 相关
    参数逐项透传。"""
    argv = [sys.executable, "-m", "atlas", "run", str(args.flow), "--root", str(root)]
    for flag, value in (("--account", args.account), ("--ou", args.ou),
                        ("--language", args.language), ("--division", args.division),
                        ("--bu", args.bu)):
        if value:
            argv += [flag, str(value)]
    if args.no_login:
        argv.append("--no-login")
    if getattr(args, "harvest", False):
        argv.append("--harvest")   # 落库线：监督子进程同跑同固化，开关不许在派发时丢
    if args.create_quota:
        argv += ["--create-quota", str(args.create_quota)]
    # 全局参数（SUPPRESS 缺省）：给了才传，位置在子命令后（全局参数前后位均可，见 _parser 注）
    for key in ("env", "mode"):
        value = getattr(args, key, None)
        if value:
            argv += [f"--{key.replace('_', '-')}", str(value)]
    if getattr(args, "headed", False):
        argv.append("--headed")
    return argv


def _child_run_env() -> dict:
    """子进程环境：继承父进程（.env/凭据/PYTHONPATH 都在其中），补两条调度器同款纪律
    （scheduling.dispatch.child_env 先例）——PYTHONIOENCODING=utf-8 防 GBK 控制台中文崩；
    PYTHONPATH 兜底补上当前包的父目录，src 布局下 `python -m atlas` 在干净 env 也能起。"""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    package_parent = str(Path(__file__).resolve().parent.parent)
    parts = [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p]
    if package_parent not in parts:
        parts.insert(0, package_parent)
    env["PYTHONPATH"] = os.pathsep.join(parts)
    return env


def _kill_process_tree(proc: subprocess.Popen) -> None:
    """按进程树强杀：`atlas run` 子进程下挂着 Chromium 与 node 驱动两个孙进程，只杀
    直接子进程会双双泄漏（孤儿浏览器占会话）。Windows 用 taskkill /T /F（列表参数不经
    shell，无转义问题）；POSIX 用进程组 SIGKILL（Popen start_new_session=True 保证子
    进程自成一组）。所有清理失败都吞掉——杀不杀得干净不改变「本条已超时」的事实，
    终态记录与返回码绝不被清理错误顶掉。"""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                           capture_output=True, timeout=15)
        else:
            import signal
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass   # 进程已退出/组不可达：目标已达成
    except Exception:
        pass
    try:
        proc.kill()   # 兜底：直接子进程本体（树杀失败的最后一手）
    except Exception:
        pass


def _supervise_case(child_argv: list[str], config, flow: dict, timeout_seconds: int) -> int:
    from .failures import FLOW_STATUS_TIMEOUT   # 终态字面量与 reporting 计数侧同源（#92）
    """看护一次子进程执行的完整生命周期，返回 CLI 退出码。

    - 正常结束：原样透传子进程 rc（0 成功 / 2 输入错误 / 1 运行失败……）；
    - 到点未退：先给 2s 宽限兜「到点瞬间恰好正常退出」的轮询竞态，仍在才整树强杀、
      补记 timeout 终态行（与 dsl.run 同款 case/env/mode/version 三元组），rc 124
      （对齐外部 timeout 的 124，超时不是 failed）；
    - 人 Ctrl-C：杀树防孤儿后按调度器同口径报 interrupted（rc 130），不吞终止信号。
    """
    from .recorder import RunRecorder
    # POSIX 下让子进程自成进程组供 killpg；Windows 忽略该参数，树杀走 taskkill /T。
    popen_kw = {} if os.name == "nt" else {"start_new_session": True}
    proc = subprocess.Popen(child_argv, cwd=str(config.root), env=_child_run_env(), **popen_kw)
    try:
        try:
            return proc.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            try:
                return proc.wait(timeout=2)   # 边界竞态宽限：恰好退出的不冤杀不重复记账
            except subprocess.TimeoutExpired:
                pass
            _kill_process_tree(proc)
            runs_path = config.reports_dir / "runs.jsonl"
            # 与 dsl.run 的 RunRecorder 同源同参：case/env/mode 一致，版本三元组照算——
            # timeout 行在 atlas summary 的版本漂移统计里与正常行同一坐标系。
            # ATLAS_RUNS_DIR 若在 env 中设置，RunRecorder 构造内自动分流到独立目录，
            # 与「并发进程各写各的」口径一致。
            recorder = RunRecorder(flow.get("id", "flow"), config.env, runs_path, config.mode,
                                   version=versioning.version_triple(
                                       private_root=versioning.private_knowledge_root(),
                                       flow=flow))
            recorder.close(FLOW_STATUS_TIMEOUT)
            print(f"case 超时（--case-timeout {timeout_seconds}s）：执行子进程已整树强杀，"
                  f"终态记 timeout → {runs_path}", file=sys.stderr)
            return 124
    except KeyboardInterrupt:
        _kill_process_tree(proc)   # 人中断看护进程：不留孤儿浏览器/驱动
        print("interrupted", file=sys.stderr)
        return 130


def _run_case_supervised(args, config, flow: dict, timeout_seconds: int) -> int:
    return _supervise_case(_case_timeout_child_argv(args, config.root), config, flow,
                           timeout_seconds)


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


def _library(args, plugin) -> int:
    """公共任务库查询（只读）：列表 / 单条详情。

    落库线「公共任务库」的读侧入口：条目是经人审批准入库的跨流程可复用动作序列
    （steps 是 DSL 步骤原文，${var} 占位不改写，复用时按数据供给接值）。
    写路径只有 review 塔的批准（红线 #9/#10），本命令零写。库文件缺失/坏形态
    如实当空库（读不残缺，knowledge.load 既有语义），不伪造条目。
    """
    data = plugin.knowledge().load("task_library")
    tasks = data.get("tasks")
    if not isinstance(tasks, list):
        tasks = []
    tasks = [t for t in tasks if isinstance(t, dict)]
    if args.name:
        hits = [t for t in tasks if t.get("name") == args.name]
        if not hits:
            known = sorted(str(t.get("name", "")) for t in tasks)
            print(f"任务库没有 {args.name!r}（现有：{'、'.join(known) if known else '空库'}）",
                  file=sys.stderr)
            return 2
        print(json.dumps(hits[0], ensure_ascii=False, indent=2))
        return 0
    print(json.dumps({
        "total": len(tasks),
        "tasks": [{"name": t.get("name", ""),
                   "steps": len(t["steps"]) if isinstance(t.get("steps"), list) else 0,
                   "source": t.get("source", ""), "created_at": t.get("created_at", "")}
                  for t in tasks]}, ensure_ascii=False, indent=2))
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
            # 选择器文本的形态判据在 locators（CLI 只做「看起来是 JSON 就必须是合法 JSON」）
            from . import locators
            remedy = locators.from_text(args.remedy, role=args.role, name=args.name)
            result = queue.approve(plugin.knowledge(), args.approve, remedy=remedy,
                                   component=args.component, element=args.element,
                                   allow_unverified=args.allow_unverified,
                                   override_reason=args.reason,
                                   require_evidence=args.require_evidence)
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
                          "next": "人填 component/element（选择器位可留空：role/name 已按候选预填）"
                                  "或 remedy 后 atlas review --apply " + str(args.template)},
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
            result = queue.apply(plugin.knowledge(), decisions,
                                 require_evidence=args.require_evidence)
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
              "（选择器候选加 --component/--element，ARIA 候选可改用 --role/--name 免手抄 CSS）；"
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
    if args.auto:
        return _explore_auto(args, config)
    from .explore import start
    # 空账号在开浏览器/登录前投递成 rc 2（判据单点在 runtime.require_account）；
    # 与 __init__ 同解析状态（此刻尚未 load_dotenv(legacy)，与 DemoRuntime 构造期一致）。
    try:
        require_account(config, resolve_account(config, args.account))
    except ValueError as exc:
        return _user_error(exc)
    try:
        trace = start(config, account=args.account, url=args.url, trace=args.trace)
    except PermissionError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"trace={trace}\n下一步：atlas explore --compile {trace}", file=sys.stderr)
    return 0


def _explore_auto(args, config) -> int:
    """`explore --auto`：LLM 自主探索（gap12）。目标进去、pending_review 草稿出来，全程无人。

    LLM 只输出白名单动作；写/提交类动作按 `classify_risk` 拦在审批门下（默认停）。
    配置缺失（--goal / LLM 三键）是用户输入错误 → rc 2；生产闸目录外 → rc 1。
    """
    import time
    from . import autopilot, llm
    from .agent import AtlasAgent
    from .experience import ExperienceJournal
    from .explore import PRODUCTION_REFUSED
    from .runtime import DemoRuntime
    goal = (args.goal or "").strip()
    if not goal:
        print("explore --auto 需要 --goal：没有目标的自主探索等于随机点击", file=sys.stderr)
        return 2
    if config.is_production:      # 生产闸必须早于开浏览器：白烧一次登录也是损失
        print(PRODUCTION_REFUSED, file=sys.stderr)
        return 1
    try:
        llm_cfg = llm.LLMConfig.from_env()
    except llm.LLMNotConfigured as exc:
        print(str(exc), file=sys.stderr)
        return 2
    trace = Path(args.trace) if args.trace else (
        config.reports_dir / f"explore_auto_{time.strftime('%Y%m%d_%H%M%S')}.jsonl")
    journal = ExperienceJournal(config.reports_dir)
    # 空账号同 run 口径：开浏览器/登录前 rc 2 一行 stderr（判据单点在 runtime.require_account）。
    try:
        require_account(config, resolve_account(config, args.account))
    except ValueError as exc:
        return _user_error(exc)
    runtime = None
    try:
        runtime = DemoRuntime(config, login=True, account=args.account).__enter__()
        page = runtime.actor.page
        if args.url:
            page.goto(args.url, wait_until="domcontentloaded", timeout=60000)
        report = autopilot.explore_auto(
            config, goal, llm=llm.LLMClient(llm_cfg), page=page, trace=trace,
            journal=journal, max_steps=args.max_steps, max_seconds=args.max_seconds,
            agent=AtlasAgent(journal=config.reports_dir / "agent_decisions.jsonl"),
            # 坑点提炼的匹配面：命中 approved 坑点的失败不重复提炼（自愈准入同语义）
            knowledge=getattr(getattr(runtime, "actor", None), "knowledge", None))
    except PermissionError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        if runtime is not None:
            runtime.__exit__(None, None, None)
    print(json.dumps({"trace": str(report.trace), "stopped_reason": report.stopped_reason,
                      "steps": len(report.steps), "rejected": report.rejected,
                      "failures": report.failures, "asserts": report.asserts,
                      "pitfall_drafts": report.pitfall_drafts,
                      "runs": str(config.reports_dir / "runs.jsonl"),
                      "drafts": report.drafts,
                      "next": "atlas review  # 探索产物只进 pending_review，审后成资产"},
                     ensure_ascii=False, indent=2))
    return 0


def _compat_gate(compat: str, fast: bool) -> None:
    """`--compat` 组合闸——**委托**唯一判据 `generator.emit_compat.compat_combo_problem`
    （lead c9b 裁决：CLI 与生成器面共用这一份 if，谁另写谁漂移 #92——旧版这里的内联
    组合条件对未知形态（"bogus"/""/"LEGACY-SUITE"）放行，与生成器面
    拒绝分叉，八组合等值扫抓出）。懒 import：非 compat 路径不触碰 emit_compat 模块。
    ValueError 原样携带判据位消息（未知形态点名 + 「需与 --fast 同用」都出自单一来源），
    入口统一转一行 stderr + rc 2，口径不变。"""
    from .generator.emit_compat import compat_combo_problem
    problem = compat_combo_problem(compat, fast)
    if problem:
        raise ValueError(problem)


def _export_pytest(args) -> int:
    """atlas export-pytest：已存在的 flow.yaml → pytest 回归代码包（gap9b B-1）。

    校验与执行/编译**同一把尺**（A.5 挂载点收益）：先过 `validate_flow`（其第一步就是
    compile 静态段——含 gap9b B-2 声明位形态闸），结构坏**不产出半成品**（校验失败时
    产物目录都未创建）；随后 `emit_pytest_package(flow=…)` 只生成不执行（红线 #9）。
    rc 口径：0 导出成功（stdout 文件清单）/ 2 用户错误（flow 不存在、结构拒绝、
    目录非空无 --force、--fast 闸门拒绝）——**3 不使用**：生成期不执行产物，
    没有「等人」终态可映射（B-1 原文；README 子命令分表由 docs-closer 收口）。
    确定性：无时间戳/随机，同 flow 重复导出内容相等。
    """
    from .generator.emit_pytest import emit_pytest_package
    try:
        _compat_gate(getattr(args, "compat", "none"), args.fast)
        flow = FlowCompiler(TaskRegistry()).load(args.flow)
        FlowCompiler(_full_registry()).validate_flow(flow)
        out_dir = args.out if args.out is not None else Path("exports/pytest") / args.flow.stem
        # compat 透传（gap11 #18 消费面）：非默认值才带 kw——compat=none 的存量调用
        # 形态逐字不变（#115），emit_pytest_package 收 compat 参后语义等价（缺省 none）。
        compat_kw = ({"compat": args.compat}
                     if getattr(args, "compat", "none") != "none" else {})
        written = emit_pytest_package(out_dir=out_dir, flow=flow,
                                      force=args.force, fast=args.fast,
                                      parallel=getattr(args, "parallel", 0), **compat_kw)
    except (OSError, ValueError, KeyError, RuntimeError, yaml.YAMLError) as exc:
        return _user_error(exc)
    print(json.dumps({"flow": str(args.flow), "out_dir": str(out_dir),
                      "fast": bool(args.fast), "written": written},
                     ensure_ascii=False, indent=2))
    return 0


def _gen(args, config) -> int:
    """atlas gen：接口文档→测试代码生成。只生成不执行（红线 #9）。

    退出码：0 成功 / 2 输入问题（文档不存在/格式无法识别/--strict 未决项）/
    3 结构自检失败（生成的 YAML validate_flow 不通过——生成器缺陷）。
    """
    import json as _json
    from . import credential_gate
    from .generator import GenOptions, generate
    from .generator.sources import SourceError
    from .generator.emit_flow import FlowEmitError
    from .generator.checklist import ChecklistError

    options = GenOptions(
        scenario=args.scenario, level=args.level, strict=args.strict,
        force=args.force, plugin=args.plugin, flow_id=args.flow_id,
        headers_file=args.headers_file, naming=args.naming,
        check_only=args.check_only, to_tower=args.to_tower,
        with_pytest=args.with_pytest, fast=args.fast, source_type=args.source_type)
    # --with-pytest 产物自包含（仅标准库 + pytest，可脱离 Atlas 运行，见 gap7 评审）：
    # 不再需要 allure-pytest 等新增运行时依赖（红线 #4）。
    # --fast（gap9）：快模式 pytest 产物，须配 --with-pytest——判据唯一实现在
    # GenOptions.validate（generate() 程序化入口同一把闸，CLI 不复制第二条 if，#92），
    # 违规走 SourceError → 一行 stderr + rc 2，不隐式改行为（#115）。
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
        options.validate()   # flag 组合判据（唯一实现 GenOptions.validate，与 generate() 同闸）
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
    # C4① 生成侧敏感头闸的收口提示（判据在 credential_gate，这里只报占位映射，不报值）：
    # `atlas gen` 的产物目录常被直接提交/贴进工单，字面鉴权值必须只剩占位键名。
    gated = [(index, name, env_key)
             for flow in result.flows
             for index, name, env_key in credential_gate.gated_placeholders_in(flow)]
    if gated:
        print(f"WARN: {len(gated)} 处字面凭据头已换成占位（值不进产物），人审接 env：",
              file=sys.stderr)
        for index, name, env_key in gated[:20]:
            print(f"  步骤{index} header {name} -> {env_key}", file=sys.stderr)
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


def _devops_sync(args, config) -> int:
    """atlas devops-sync：把 Allure 结果集同步到美的 DevOps 平台（用例库/测试计划）。

    退出码：0 成功（含 dry-run）/ 2 用户错误（配置缺失/无可用用例/MCP 认证失败）。
    边界：**默认 dry-run 只算不传**（对外平台同步属敏感操作，AGENTS §4 Ask first）；
    --execute 才真实调用 MCP。真实通路依赖 mcp.json 中 devops-mcp-* 的有效 key。
    """
    import json as _json
    from .devops_sync import sync_plan, DevopsSyncError

    try:
        result = sync_plan(
            config.root,
            suite_filter=getattr(args, "suite", None),
            execute=getattr(args, "execute", False),
            config_path=getattr(args, "config", None),
            results_dir=getattr(args, "results_dir", None),
            mcp_server=getattr(args, "mcp_server", None),
        )
    except DevopsSyncError as exc:
        return _user_error(exc)
    except (OSError, ValueError) as exc:
        return _user_error(exc)

    if getattr(args, "format", "json") == "text":
        mode = "真实同步" if result.get("executed") else "DRY-RUN（只算不传）"
        print(f"atlas devops-sync [{mode}]")
        print(f"  用例：{result.get('case_count', 0)}"
              f"（导入 {result.get('import_count', 0)} / 复用 {result.get('reuse_count', 0)}）")
        if result.get("test_plan_id"):
            print(f"  测试计划：{result['test_plan_id']}")
        if not result.get("executed"):
            print("  提示：加 --execute 才会真实上传（默认只算不传）")
    else:
        print(_json.dumps(result, ensure_ascii=False, indent=2))
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


def _schedule(args, config) -> int:
    """atlas schedule：调度台账与一次性 tick 的 CLI 面（design_c1 §7）。

    rc 口径：2 用户错误（坏 id / 坏 cron / 坏 tz / 缺 tz / 缺 flow / 写档未确认）；
    0 一切调度结果（defer、none、drop、准点跑成都算正常答复——cron 不看 rc 干活，人看
    `schedule_runs.jsonl` 与 `schedule status`）；1 仅派发子进程失败。
    Atlas 不代注册 OS 任务、不做常驻进程（Ask-first 边界）：`crontab` 只打印成品行。
    """
    from .scheduling import dispatch
    from .scheduling import state as sched_state
    from .scheduling.cron_parse import load_tz, min_gap_minutes, next_slot, now_utc, parse_expr

    root = Path(config.root)
    tasks_file = sched_state.tasks_path(root)
    runs_file = sched_state.runs_path(root)
    command = args.schedule_command
    try:
        moment = now_utc()
        doc = sched_state.load_doc(tasks_file)
        if command == "add":
            return _schedule_add(args, doc, tasks_file, root, moment,
                                 parse_expr, load_tz, next_slot)
        if command == "list":
            rows = [_schedule_row(doc["tasks"][tid]) for tid in sorted(doc["tasks"])]
            print(json.dumps(rows, ensure_ascii=False, indent=2))
            if not rows:
                print("台账为空：atlas schedule add <id> --flow <flow.yaml> "
                      "--cron '*/30 1-8 * * *' --tz Europe/Amsterdam", file=sys.stderr)
            return 0
        if command == "status":
            ids = [args.id] if getattr(args, "id", None) else sorted(doc["tasks"])
            if args.id:
                sched_state.get_task(doc, args.id, source=tasks_file)   # 坏 id 点名（rc 2）
            totals = sched_state.skipped_totals(runs_file)
            outcomes = {}
            for row in sched_state.read_runs(runs_file):
                tid = row.get("task_id")
                outcome = row.get("outcome")
                # 只数真实终态：outcome 缺失/非字符串不当成 "None" 桶（不冒充一个没发生的分类）
                if isinstance(tid, str) and isinstance(outcome, str) and outcome:
                    counts = outcomes.setdefault(tid, {})
                    counts[outcome] = counts.get(outcome, 0) + 1
            payload = []
            paused = []
            for tid in ids:
                task = doc["tasks"].get(tid)
                if not isinstance(task, dict):
                    continue
                entry = _schedule_row(task)
                entry["skipped_total"] = totals.get(tid, 0)
                entry["outcomes"] = outcomes.get(tid, {})
                entry["next_slot"] = _preview_next(task, moment, parse_expr, load_tz, next_slot)
                payload.append(entry)
                if not task.get("enabled", True) and task.get("auto_paused_reason"):
                    paused.append((tid, task["auto_paused_reason"]))
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            for tid, reason in paused:
                # §3 表 9：AUTO-PAUSED 必须醒目并带原因——「环境挂一整晚」停在一个有原因的任务上
                print(f"AUTO-PAUSED: {tid} 连续 5 次失败已自动停用（原因：{reason}）；"
                      f"人工确认后用 atlas schedule enable {tid}", file=sys.stderr)
            return 0
        if command in ("enable", "disable", "remove"):
            task = sched_state.get_task(doc, args.id, source=tasks_file)
            if command == "remove":
                del doc["tasks"][args.id]
                sched_state.save_doc(tasks_file, doc)
                print(json.dumps({"removed": args.id, "history_kept": str(runs_file)},
                                 ensure_ascii=False))
                return 0
            updated = sched_state.apply_patch(task, {"enabled": command == "enable"},
                                              root=root, now=moment)
            doc["tasks"][args.id] = updated
            sched_state.save_doc(tasks_file, doc)
            print(json.dumps(_schedule_row(updated), ensure_ascii=False, indent=2))
            return 0
        if command == "edit":
            task = sched_state.get_task(doc, args.id, source=tasks_file)
            patch = _schedule_edit_patch(args, task, load_tz)
            if not patch:
                raise ValueError("edit 没给要改的字段（--flow/--cron/--tz/--at/--manual/"
                                 "--run-arg/--max-duration-min/--i-accept-writes）")
            updated = sched_state.apply_patch(task, patch, root=root, now=moment)
            if "flow" in patch or "schedule" in patch:
                # 改配置当场重过入库闸（与 add 同一判据，不留第二份）
                problems = sched_state.admission_problems(updated, root=root,
                                                          registry=_full_registry())
                _schedule_writes_gate(problems, updated.get("writes_acknowledged"))
            doc["tasks"][args.id] = updated
            sched_state.save_doc(tasks_file, doc)
            out = _schedule_row(updated)
            out["next_slot"] = _preview_next(updated, moment, parse_expr, load_tz, next_slot)
            print(json.dumps(out, ensure_ascii=False, indent=2))
            _schedule_knowledge_warn(updated)   # 编辑也是人在场的时刻，同样刷新可见性
            return 0
        if command == "run":
            return dispatch.tick(args.id, config, dry=bool(args.dry_run))
        if command == "crontab":
            task = sched_state.get_task(doc, args.id, source=tasks_file)
            print(_schedule_crontab(args, task, root, moment, parse_expr, load_tz,
                                    min_gap_minutes))
            return 0
        return 2
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        return _user_error(exc)
    except KeyboardInterrupt:
        # 人 Ctrl-C 调度器：dispatch 已记 interrupted（不计败），这里只做退出收尾
        print("interrupted", file=sys.stderr)
        return 130


def _schedule_add(args, doc, tasks_file, root, moment, parse_expr, load_tz, next_slot) -> int:
    """add：入库判据全走 `scheduling.state`（与 edit 同源）；半成品不落账。"""
    from .scheduling import state as sched_state
    from .scheduling.dispatch import guard_fast_cdp

    if args.id in doc["tasks"]:
        raise ValueError(f"任务 {args.id!r} 已存在（改配置用 atlas schedule edit）")
    # 登记期闸门（lead 2026-11-09：报错要落在人还能改的那个时刻——脏 run_args 若等
    # 派发才 raise，每轮记一次连败、攒满 5 次 AUTO-PAUSED，表象是「巡检无故连败」，
    # 根因藏在派发栈里）。判据与派发纵深共用 `dispatch.guard_fast_cdp` 单一实现（#92）；
    # 派发时的 raise 保留——防台账被手改。异常由 _schedule 顶层统一转一行 stderr + rc 2。
    guard_fast_cdp(list(args.run_args or []))
    spec = _schedule_add_spec(args)
    task = sched_state.build_task(task_id=args.id, flow=str(args.flow), schedule=spec,
                                  root=root, run_args=args.run_args,
                                  max_duration_min=args.max_duration_min,
                                  writes_acknowledged=args.accept_writes, now=moment)
    problems = sched_state.admission_problems(task, root=root, registry=_full_registry())
    _schedule_writes_gate(problems, args.accept_writes)
    doc["tasks"][args.id] = task
    sched_state.save_doc(tasks_file, doc)
    out = _schedule_row(task)
    out["write_steps"] = problems["write_steps"]
    out["next_slot"] = _preview_next(task, moment, parse_expr, load_tz, next_slot)
    out["wiring"] = f"atlas schedule crontab {args.id}"
    print(json.dumps(out, ensure_ascii=False, indent=2))
    _schedule_knowledge_warn(task)   # 入库当场：builtin 无人值守风险点名（判据在 versioning）
    return 0


def _schedule_add_spec(args) -> dict:
    """add 的 schedule 归一：cron / once / manual 恰选一个（不留「都写了」的歧义形态）。"""
    chosen = [bool(args.cron), bool(args.at), bool(args.manual)]
    if sum(chosen) != 1:
        raise ValueError("必须恰选一种调度：--cron <5段表达式>（配 --tz）、--at <ISO> 或 --manual")
    if args.manual:
        return {"kind": "manual"}
    if args.at:
        spec = {"kind": "once", "at": args.at}
        if args.tz:
            spec["tz"] = args.tz
        return spec
    return {"kind": "cron", "expr": args.cron, "tz": args.tz}


def _schedule_edit_patch(args, task: dict, load_tz) -> dict:
    """edit 的字段补丁：只带用户显式给了的键；未给的 schedule 成分从现值继承。"""
    from .scheduling.dispatch import guard_fast_cdp
    patch: dict = {}
    if getattr(args, "flow", None) is not None:
        patch["flow"] = str(args.flow)
    if getattr(args, "run_args", None) is not None:
        # 登记期闸门（与 add 同判据 dispatch.guard_fast_cdp，不留第二份 #92）：
        # 人还能改的时刻当场 rc2 点名，别等每轮派发 raise 攒连败到 AUTO-PAUSED
        guard_fast_cdp(list(args.run_args))
        patch["run_args"] = list(args.run_args)
    if getattr(args, "max_duration_min", None) is not None:
        patch["max_duration_min"] = args.max_duration_min
    if getattr(args, "accept_writes", False):
        patch["writes_acknowledged"] = True
    existing = task.get("schedule") or {}
    cron = getattr(args, "cron", None)
    at = getattr(args, "at", None)
    tz = getattr(args, "tz", None)
    manual = getattr(args, "manual", False)
    if manual:
        patch["schedule"] = {"kind": "manual"}
    elif at is not None:
        spec = {"kind": "once", "at": at}
        if tz:
            spec["tz"] = tz
        patch["schedule"] = spec
    elif cron is not None:
        patch["schedule"] = {"kind": "cron", "expr": cron,
                             "tz": tz if tz else existing.get("tz")}
    elif tz:
        if existing.get("kind") == "cron":
            patch["schedule"] = {"kind": "cron", "expr": existing.get("expr"), "tz": tz}
        elif existing.get("kind") == "once":
            patch["schedule"] = {"kind": "once", "at": existing.get("at"), "tz": tz}
        else:
            raise ValueError("--tz 只对 cron/once 有意义（manual 没有时区语义）")
    if tz:
        load_tz(tz)   # 当场验证可加载（X6 文案），不等 tick 才炸
    return patch


def _schedule_writes_gate(problems: dict, acknowledged) -> None:
    """§6.2 只读护栏：含写步骤且没有人的显式确认 → 拒绝入库（M14 产出侧强制确认）。"""
    writes = problems.get("write_steps") or []
    if writes and not acknowledged:
        raise ValueError(f"flow 含写步骤 {writes}：无人值守默认只读。确要无人跑写档必须显式 "
                         f"--i-accept-writes（这是人的决定记录；调度器自身永不带 --approve，"
                         f"红线 #13/#2）")


def _schedule_row(task: dict) -> dict:
    """台账行的展示形态（list/status/add/edit 共用，不留第二份字段清单）。"""
    schedule = task.get("schedule") or {}
    row = {"id": task.get("id"), "enabled": bool(task.get("enabled", True)),
           "kind": schedule.get("kind"), "flow": task.get("flow"),
           "last_scheduled_for": task.get("last_scheduled_for"),
           "consecutive_failures": task.get("consecutive_failures", 0),
           "auto_paused_reason": task.get("auto_paused_reason"),
           "max_duration_min": task.get("max_duration_min"),
           "writes_acknowledged": bool(task.get("writes_acknowledged")),
           "knowledge_source": task.get("knowledge_source"),
           "run_args": list(task.get("run_args") or [])}
    if schedule.get("kind") == "cron":
        row["cron"] = schedule.get("expr")
        row["tz"] = schedule.get("tz")
    elif schedule.get("kind") == "once":
        row["at"] = schedule.get("at")
    return row


_KNOWLEDGE_BUILTIN_WARN = ("WARN: knowledge_source=builtin -- unattended rounds run on the "
                           "bundled fictional knowledge (pitfalls/contracts/session defaults), "
                           "not your private KB; cron/schtasks does not inherit an exported "
                           "ATLAS_KNOWLEDGE_DIR, so put it in the project .env (load_dotenv "
                           "reads it) or bake it into the crontab line")

# crontab/schtasks 成品行里私有库路径缺失时的刺眼占位（绝不留空——留空会被复制者当正常行忽略，
# 占位串逼填或改 .env；即便原样粘贴也是无效路径→builtin，与 add 的 WARN 同口径可见）
_KB_PLACEHOLDER = "__FILL_ABSOLUTE_PATH_TO_PRIVATE_KB__"


def _schedule_knowledge_warn(task: dict) -> None:
    """入库/编辑当场让「无人值守会拿包内虚构知识跑」可见（判据只在 versioning 一处，这里只展示）。

    为什么无条件 WARN：连离线骨架也可能哪天接真实 flow；WARN 成本是一行 stderr，而漏报是整轮
    静默跑错知识（engine/dsl 都不报错，回退本就合法）。真正拦下来要靠接线带 env——见 crontab。
    """
    if task.get("knowledge_source") == "builtin":
        print(_KNOWLEDGE_BUILTIN_WARN, file=sys.stderr)


def _preview_next(task: dict, moment, parse_expr, load_tz, next_slot):
    """下一槽预览（cron 才有意义）：只读计算，不改台账。"""
    from .scheduling import state as sched_state

    schedule = task.get("schedule") or {}
    if schedule.get("kind") == "cron":
        slot = next_slot(parse_expr(schedule.get("expr")), load_tz(schedule.get("tz")), moment)
        return sched_state.iso(slot)
    if schedule.get("kind") == "once":
        return schedule.get("at")
    return None


def _schedule_crontab(args, task: dict, root: Path, moment, parse_expr, load_tz,
                      min_gap_minutes) -> str:
    """接线成品行（X8 + 评审 C1-A 裁决），Atlas 绝不代跑 schtasks/crontab。

    - Linux：**expr 逐字复制**进 crontab（窗外语义保真：窗外不补发，窗外错过的由窗内
      下一个 tick 按 catch-up 补最近一轮）。cronie **不解析** task 里的 tz——它只按本机墙钟
      调起 tick，tz 是 Atlas 决策（窗口/水位线）内部的事。这条差异必须让复制命令的人看见。
    - Windows：`schtasks /SC MINUTE /MO <expr 最小间隔>`（与 pi 常驻 tick 同语义：窗外照样
      tick，窗外错过的以 catch-up 补）。间隔取表达式实际最小相邻间隙。
    """
    schedule = task.get("schedule") or {}
    kind = schedule.get("kind")
    task_id = task.get("id")
    if kind == "manual":
        raise ValueError("manual 任务没有 OS 接线：手动跑一轮用 `atlas schedule run "
                         f"{task_id}`（或直接 atlas run <flow>）")
    if kind == "once":
        raise ValueError("once 不生成接线行：cron 语义里没有「只跑一次」。要么用等价 5 段表达式"
                         "登记那个时刻（分 时 日 月 周），要么留周期任务让 catch-up 补最近一轮")
    expr = schedule.get("expr")
    tz_name = schedule.get("tz")
    spec = parse_expr(expr)
    tz = load_tz(tz_name)
    platform = args.platform
    if platform == "auto":
        platform = "windows" if sys.platform.startswith("win") else "linux"
    python = sys.executable or ("python" if platform == "windows" else "python3")
    log_path = root / "reports" / "schedule" / "cron.log"
    # cron/schtasks 的父 env 通常不含交互式 shell export 的 ATLAS_KNOWLEDGE_DIR（cron 只给
    # HOME/PATH），于是无人值守轮次会静默回退包内虚构知识。成品行带上当前解析到的私有库路径
    # （取 versioning.private_knowledge_root 单一真相，已覆盖 .env/plugin/env）；解析不到就放
    # 一个刺眼占位串而非留空——留空会被复制者忽略，占位串逼他要么填、要么写进 .env（Lead 拍板 1）。
    kb_root = versioning.private_knowledge_root()
    kb_value = kb_root.as_posix() if kb_root is not None else _KB_PLACEHOLDER
    notes = []
    if kb_root is None:
        notes.append(f"WARN: 本机未解析到 ATLAS_KNOWLEDGE_DIR——下面成品行用了占位 {_KB_PLACEHOLDER}，"
                     "请替换成私有库绝对路径，或把该键写进项目 .env（load_dotenv 会读），"
                     "否则无人值守轮次跑包内虚构知识（与 schedule add 的 WARN 同源）")
    if platform == "linux":
        # crontab 的环境比交互 shell 干净：不注入 PYTHONPATH/ATLAS_KNOWLEDGE_DIR 时 `python -m atlas`
        # 在 src 布局起不来、私有知识也回落 builtin
        line = (f'{expr} cd {root.as_posix()} && ATLAS_KNOWLEDGE_DIR="{kb_value}" '
                f'PYTHONPATH={(root / "src").as_posix()} '
                f'{python} -m atlas schedule run {task_id} >> {log_path.as_posix()} 2>&1')
        notes.append("Linux：expr 已逐字复制（窗外不补发；窗外错过由窗内下一 tick 以 catch-up 补最近一轮）")
        notes.append("Linux：cronie 按本机墙钟调起，不解析任务里的 tz——tz 只用于 Atlas 侧窗口/水位线判定")
    else:
        gap = min_gap_minutes(spec, tz, moment)
        if gap is None:
            raise ValueError(f"无法从 {expr!r} 推出最小固定间隔（窗内排不出两个槽）——"
                             "schtasks /SC MINUTE 表达不了它，请用 /SC DAILY/WEEKLY/MONTHLY 手工建任务")
        mo = int(round(gap))
        if mo < 1:
            mo = 1
        if mo > 1439:
            raise ValueError(f"最小间隔 {mo} 分钟超出 schtasks /SC MINUTE 上限 1439——"
                             "请用 /SC DAILY/WEEKLY/MONTHLY 手工建任务（Atlas 不猜别的 /SC 形态）")
        src_path = str(root / "src")
        kb_native = str(kb_root) if kb_root is not None else _KB_PLACEHOLDER
        # /TR 外层已有一对引号，内层 `set "K=V"` 的引号必须转义成 \" ——否则 schtasks 收到的
        # /TR 在第一个内层引号处截断（Microsoft 文档示例同款写法）。
        inner = '\\"'
        tr = (f'cmd /c cd /d {str(root)} && set {inner}ATLAS_KNOWLEDGE_DIR={kb_native}{inner} && '
              f'set {inner}PYTHONPATH={src_path}{inner} && '
              f'{python} -m atlas schedule run {task_id}')
        line = f'schtasks /Create /SC MINUTE /MO {mo} /TN atlas-{task_id} /TR "{tr}"'
        notes.append(f"Windows：每 {mo} 分钟 tick（窗外也 tick，错过的以 catch-up 补最近一轮——与 Linux 形态有意不同）")
        notes.append("Windows：/TR 引号形态须真实 schtasks 冒烟一次（design_c1 §10 未验证项）；"
                     "重复创建先 schtasks /Delete /TN atlas-" + str(task_id) + " /F 或加 /F")
    notes.append("通用：调度目录必须在**本地文件系统**——同步盘/NFS（OneDrive/坚果云/NFSv2）上 "
                 "O_EXCL 锁语义不可靠，双机各建各的锁不在支持范围（评审 C1-D）")
    notes.append("通用：tick 按本机墙钟；NTP 漂移超过 2 分钟会自然表现为 catch-up，不额外校时")
    # X8 文档义务（design-plan-p2 §3-5，W6 C9）：接线人只看 OS 侧日志时最需要知道的是
    # 「rc3 不是挂了」——OS cron 不看退出码干活，但监控常对非 0 报警。判据仍只在
    # decision/dispatch 两处（这里只陈述事实，CLI 不复制第二份，#92）。
    notes.append("通用：flow 遇人工步骤（`AwaitingHuman`）时子进程 rc=3=**等人**——调度侧记 "
                 "outcome=waiting、**不计连败**（不会 AUTO-PAUSED 停掉正需要人看的任务），"
                 "本轮 `schedule run` 答 rc 0，水位线照常推进；人处置后下一轮重跑到该步")
    for note in notes:
        print(note, file=sys.stderr)
    return line


if __name__ == "__main__":
    raise SystemExit(main())
