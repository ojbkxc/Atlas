"""Atlas 文档→测试代码生成器（Gap3）。

从接口文档（OpenAPI3/Swagger2/Markdown/纯文本）离线生成可被人审、可被
atlas compile 校验、可被 atlas run 执行的 Atlas 资产。**只生成不执行**（红线 #9）。

数据流：文档 → sources 嗅探 → Endpoint[] → emit_flow/emit_contract/emit_probe
       → checklist 自检 → report.json + flow YAML + 契约候选 + 探针骨架
"""
from __future__ import annotations
import json
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .model import Endpoint
from .sources import parse_source, SourceError
from .emit_flow import emit_flow, FlowEmitError
from .emit_contract import emit_contracts
from .emit_probe import emit_probe_skeleton
from .checklist import check_flow, ChecklistError, scan_placeholders_only
from .dsl_utils import slugify

# 门面公开契约（lead 对 A-05 的裁决=补 `__all__` 认门面，而非删两条错误类 import）：
# rc 归因靠 FlowEmitError/ChecklistError 这两族，它们属**用户可见 API**，不是死代码。
# 只列契约名——`Any` 那类死导入走 A-04 删掉，不许混进门面。
__all__ = ["GenOptions", "generate", "FlowEmitError", "ChecklistError"]


@dataclass
class GenOptions:
    scenario: str = "auto"          # a/b/c/d/auto
    level: str = "auto"            # auto/minimal/high/core
    strict: bool = False
    force: bool = False
    plugin: str = "demo"
    flow_id: str = ""
    headers_file: str = ""         # JSON/YAML 全局头
    naming: str = "resp_{n}_{slug}"
    division: str = ""
    check_only: bool = False
    to_tower: bool = False
    with_pytest: bool = False
    fast: bool = False                # gap9 快模式 pytest 产物（须配 with_pytest；判据见 validate）
    source_type: str = "auto"

    def validate(self) -> None:
        """flag 组合判据**唯一实现**（CLI 与 generate() 共用这一把闸，不留第二份 if——#92）。

        --fast 不隐式打开 --with-pytest（#115：静默改行为会把用户显式决定偷换成默认）：
        快模式产物是 pytest 回归包的增量形态，单独 --fast 没有作用对象，当场点名拒绝。
        """
        if self.fast and not self.with_pytest:
            raise SourceError(
                "--fast 需与 --with-pytest 同用（快模式产物是 pytest 回归包的扩展形态，"
                "--fast 不会隐式打开 --with-pytest）")


@dataclass
class GenResult:
    flows: list[dict] = field(default_factory=list)          # flow dicts
    contracts: dict = field(default_factory=dict)
    probe_source: str = ""
    report: dict = field(default_factory=dict)
    compile_ok: bool = True
    low_confidence: list[dict] = field(default_factory=list)
    manual_assertions: list[str] = field(default_factory=list)
    conflicts: list[dict] = field(default_factory=list)
    placeholders: list[dict] = field(default_factory=list)
    written: list[str] = field(default_factory=list)
    source_type: str = ""
    scenario: str = ""


def _load_headers(headers_file: str) -> dict:
    """读全局头文件（JSON/YAML）；值写成 ${ENV} 占位（不写真实值）。"""
    if not headers_file:
        return {}
    path = Path(headers_file)
    if not path.exists():
        raise SourceError(f"头文件不存在：{path}")
    text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise SourceError(f"头文件 {path.name} 顶层必须是映射")
    return {str(k): str(v) for k, v in data.items()}


def generate(source_path: Path, out_dir: Path | None = None,
             options: GenOptions | None = None, *, registry=None) -> GenResult:
    """主入口：文档 → 生成资产。纯离线、零网络、零执行。

    registry 用于 checklist 编译自检（复用 cli._full_registry）；不传则跳过结构自检
    （仅 check-only 路径需要）。
    """
    options = options or GenOptions()
    options.validate()   # 与 CLI 同一把判据闸（GenOptions.validate，gap9 C.3）
    endpoints, stype = parse_source(source_path, options.source_type)
    result = GenResult(source_type=stype, scenario=options.scenario)
    global_headers = _load_headers(options.headers_file)
    # 默认全局鉴权头（BUG-920 防线：isc-app 业务识别头并入每个 step）
    merged_headers = dict(global_headers)
    if "isc-app" not in merged_headers:
        merged_headers["isc-app"] = "${ISC_APP_HEADER}"

    # 场景判定（auto）
    if options.scenario == "auto":
        from .emit_flow import _detect_scenario
        scenario = _detect_scenario(endpoints)
    else:
        scenario = options.scenario
    result.scenario = scenario

    # 场景 C：批量，一接口一 flow；其余场景合一个 flow
    if scenario == "c" or len(endpoints) > 1 and scenario == "auto" and _is_batch(endpoints):
        batch_flows, all_manual, all_low = _emit_batch(
            endpoints, scenario="c", plugin=options.plugin,
            global_headers=merged_headers, naming=options.naming)
    else:
        flow, manual, low = emit_flow(
            endpoints, scenario=scenario, plugin=options.plugin,
            flow_id=options.flow_id, global_headers=merged_headers,
            naming=options.naming, division=options.division)
        batch_flows = [flow]
        all_manual = manual
        all_low = low

    result.flows = batch_flows
    result.low_confidence = all_low
    result.manual_assertions = all_manual

    # 契约候选
    result.contracts = emit_contracts(endpoints, level_mode=options.level,
                                       source_label=f"{stype}:{source_path.name}")
    result.conflicts = result.contracts.get("conflicts", [])

    # 探针骨架
    probe_name = slugify(endpoints[0].summary or source_path.stem,
                         fallback="GeneratedProbe") + "Probe"
    result.probe_source = emit_probe_skeleton(endpoints, scenario=scenario,
                                               entity_name=probe_name)

    # checklist 自检（每个 flow）
    placeholders: list[dict] = []
    for flow in batch_flows:
        if registry is not None:
            report = check_flow(flow, registry)
            placeholders.extend(report["placeholders"])
            if report["dangling"]:
                result.manual_assertions.extend(
                    f"悬空引用：{d['ref']}（{d['reason']}）" for d in report["dangling"])
        else:
            placeholders.extend(scan_placeholders_only(flow))
    result.placeholders = placeholders

    # --strict：低置信度/占位符未接 env 一律 rc 2（由 CLI 处理退出码）
    # 先建报告（report.json 要写进产物目录，必须在 _write_outputs 之前）
    result.report = _build_report(source_path, stype, scenario, endpoints,
                                    batch_flows, result, options)
    # 写文件（除非 check-only）
    written: list[str] = []
    if not options.check_only and out_dir is not None:
        written = _write_outputs(out_dir, batch_flows, result, probe_name,
                                 source_path, options)
        # --with-pytest：可选产出「可独立运行」的 pytest 回归代码包（只生成不执行，红线 #9）。
        # 形态评审见 docs/gaps/gap7-regression-code-eval.md：pytest 用例 + 页对象库（派生自组件库）
        # + 自包含底座（仅标准库，可脱离 Atlas 运行）。批量场景一 flow 一包。
        # --fast（gap9）：透传给 emit_pytest_package，由其在包内追加 _atlas_fast.py + conftest.py
        # 快底座（底座模板判据单一来源=atlas.generator.emit_fast，本文件零模板文本）。
        # 存量形态零涟漪（#115）：fast=False 的调用形态逐字不变——kw 仅在 True 时带上，
        # emit_pytest_package 落地 fast 参数（缺省 False）后两种写法产出字节等价。
        if options.with_pytest:
            from .emit_pytest import emit_pytest_package, PytestEmitError
            fast_kw = {"fast": True} if options.fast else {}
            try:
                if len(batch_flows) > 1:
                    for flow in batch_flows:
                        pkg_dir = out_dir / "pytest" / slugify(
                            str(flow.get("id") or "flow"), fallback="flow")
                        written.extend(emit_pytest_package(
                            out_dir=pkg_dir, flow=flow, force=True, **fast_kw))
                else:
                    written.extend(emit_pytest_package(
                        out_dir=out_dir / "pytest", endpoints=endpoints,
                        flow_id=(batch_flows[0]["id"] if batch_flows else options.flow_id),
                        global_headers=merged_headers, naming=options.naming,
                        force=True, **fast_kw))
            except (PytestEmitError, ValueError) as exc:
                # FastEmitError 同为 ValueError 子类（gap9 C.2 裁决：emit_pytest 统一转 ValueError）——
                # 底座/conftest 目录冲突这类「写出失败」一律点名，不静默出半截包。
                raise SourceError(f"--with-pytest 产物写出失败：{exc}") from exc
    result.written = written

    return result


def _is_batch(endpoints: list[Endpoint]) -> bool:
    """批量判定：>=3 端点且无链路标注 → 场景 C（一接口一 flow）。"""
    return len(endpoints) >= 3 and not any(ep.flow_override for ep in endpoints)


def _emit_batch(endpoints: list[Endpoint], *, scenario: str, plugin: str,
                global_headers: dict, naming: str) -> tuple[list[dict], list[str], list[dict]]:
    """场景 C：一接口一 flow。"""
    flows, all_manual, all_low = [], [], []
    for ep in endpoints:
        flow, manual, low = emit_flow(
            [ep], scenario="a", plugin=plugin,
            flow_id=slugify(ep.summary or ep.path, fallback="op"),
            global_headers=global_headers, naming=naming)
        flows.append(flow)
        all_manual.extend(manual)
        all_low.extend(low)
    return flows, all_manual, all_low


def _write_outputs(out_dir: Path, flows: list[dict], result: GenResult,
                   probe_name: str, source_path: Path, options: GenOptions) -> list[str]:
    """写产物：flows/ contracts/ probes/ report.json README.md。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    flows_dir = out_dir / "flows"
    flows_dir.mkdir(exist_ok=True)
    for flow in flows:
        fid = flow["id"]
        path = flows_dir / f"{fid}.yaml"
        _write_yaml(path, flow)
        written.append(str(path))
    # index.yaml（批量）
    if len(flows) > 1:
        index_path = flows_dir / "index.yaml"
        index = {"flows": [{"id": f["id"],
                            "endpoints": [s.get("path") for s in f.get("steps", [])]}
                           for f in flows]}
        _write_yaml(index_path, index)
        written.append(str(index_path))
    # 契约候选
    contracts_dir = out_dir / "contracts"
    contracts_dir.mkdir(exist_ok=True)
    cpath = contracts_dir / "contracts.candidate.json"
    cpath.write_text(json.dumps(result.contracts, ensure_ascii=False, indent=2),
                     encoding="utf-8")
    written.append(str(cpath))
    # 探针骨架
    probes_dir = out_dir / "probes"
    probes_dir.mkdir(exist_ok=True)
    ppath = probes_dir / f"{probe_name}.candidate.py"
    ppath.write_text(result.probe_source, encoding="utf-8")
    written.append(str(ppath))
    # report.json
    report_path = out_dir / "report.json"
    report_path.write_text(json.dumps(result.report, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    written.append(str(report_path))
    # README（人审指引 + 未执行横幅）
    readme = _build_readme(source_path, flows, result, probe_name, options)
    readme_path = out_dir / "README.md"
    readme_path.write_text(readme, encoding="utf-8")
    written.append(str(readme_path))
    return written


def _write_yaml(path: Path, data: dict) -> None:
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False,
                                   default_flow_style=False),
                    encoding="utf-8")


def _build_report(source_path: Path, stype: str, scenario: str,
                  endpoints: list[Endpoint], flows: list[dict],
                  result: GenResult, options: GenOptions) -> dict:
    """report.json 骨架（§7.4，人审 30 秒材料）。"""
    import hashlib
    flow_id = flows[0]["id"] if flows else ""
    history_id = hashlib.sha1(f"gen|{flow_id}".encode()).hexdigest()[:16]
    return {
        "source": str(source_path),
        "source_type": stype,
        "flow_id": flow_id,
        "scenario": scenario,
        "endpoints": [{"method": ep.method, "path": ep.path, "summary": ep.summary}
                      for ep in endpoints],
        "compile": {"ok": result.compile_ok},
        "contracts": [{"method": c["method"], "path": c["path"], "level": c["level"]}
                      for c in result.contracts.get("contracts", [])],
        "probes": [{"entity": result.probe_source.split("class ")[1].split("(")[0]
                    if "class " in result.probe_source else "", "strategy": "find_only"}],
        "unresolved_placeholders": result.placeholders,
        "low_confidence": result.low_confidence,
        "manual_assertions": result.manual_assertions,
        "conflicts": result.conflicts,
        "allure": {
            "name": flow_id,
            "history_id": history_id,
            "labels": [f"epic:{options.plugin}", f"feature:{source_path.stem}"],
        },
        "next": [
            "人审 report.json",
            f"atlas compile {out_flow_hint(flows)}",
            "atlas run <flow>（鉴权值/写操作人工确认后）",
            "atlas review",
        ],
    }


def out_flow_hint(flows: list[dict]) -> str:
    if len(flows) == 1:
        return f"flows/{flows[0]['id']}.yaml"
    return "flows/index.yaml（批量，逐条 compile）"


def _build_readme(source_path: Path, flows: list[dict], result: GenResult,
                  probe_name: str, options: GenOptions) -> str:
    flow_ids = ", ".join(f["id"] for f in flows)
    readme = (
        f"# atlas gen 产物\n\n"
        f"来源：{source_path}\n\n"
        f"**横幅：本产物只生成不执行。鉴权值、写操作、建单须人工确认后手动 `atlas run`。**\n\n"
        f"生成 flow id：{flow_ids}\n\n"
        f"探针候选：{probe_name}.candidate.py（只实现 find()，create() 恒 NotImplementedError）\n\n"
        f"## 人审与执行指引\n\n"
        f"1. 审 report.json：低置信度项、占位符、待填必填参数、manual_assertions\n"
        f"2. `atlas compile flows/<id>.yaml` 离线校验（不登录、不执行）\n"
        f"3. 接鉴权值（${{ENV}} 占位）后 `atlas run flows/<id>.yaml`（写场景确认 read_only:false）\n"
        f"4. 产物进 `atlas review` 审核塔（trace_step，不可 --approve，人工写成 flow YAML 后 compile）\n\n"
        f"## Allure 汇入\n\n"
        f"主路径：`atlas run` → reports/runs.jsonl → `atlas allure reports/runs.jsonl --out reports/allure-results`\n"
        f"生成器不自己写 Allure（生成阶段无结果，写了=伪造）。\n"
    )
    if options.fast:
        readme += _fast_readme_section()
    return readme


def _fast_readme_section() -> str:
    """快模式产物 README 段（gap9 C.4）。env 旋钮清单唯一来源 = emit_fast.ENV_KEYS，
    此处只渲染不复制——清单出现第二份手抄本即 #92 漂移温床。"""
    from .emit_fast import ENV_KEYS   # 懒加载：仅 --fast 路径触碰（判据闸已保证 with_pytest=True）
    env_lines = "\n".join(
        f"- `{k}`：{'未设（缺配即 EnvBlocked，产物如实拒绝）' if v is None else v}"
        for k, v in sorted(ENV_KEYS.items()))
    return (
        "\n## 快模式（--fast）\n\n"
        "`pytest/` 包内含快底座 `_atlas_fast.py` + `conftest.py`：单浏览器/单登录、语义等待、\n"
        "零成功截图、用例看门狗、写操作闸、Atlas 兼容 runs.jsonl。\n\n"
        "依赖栈：`pytest + playwright`（playwright 为 Atlas 既有运行时依赖，非新增；\n"
        "产物禁用 allure-pytest / pytest-playwright / pytest-timeout / requests，见 gap9 F9）。\n\n"
        "人审接线后消费命令（`<pkg>` 为 `pytest/`，批量场景为 `pytest/<flow id>/`）：\n\n"
        "1. `pytest <pkg> -q`\n"
        "2. `atlas allure <pkg>/reports/runs.jsonl --out <pkg>/reports/allure-results`\n\n"
        "运行期旋钮（默认值；唯一清单在 `atlas.generator.emit_fast.ENV_KEYS`）：\n\n"
        f"{env_lines}\n\n"
        "**红线警示**：写操作腿默认被 `ATLAS_FAST_ALLOW_WRITES` 闸拦下记 env_blocked（放行是人的显式\n"
        "决定）；生产环境保护仍由 Atlas engine 兜底；UI 腿选择器须人审接线，产物只生成不执行。\n"
    )
