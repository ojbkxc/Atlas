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
from typing import Any

import yaml

from .model import Endpoint
from .sources import parse_source, SourceError
from .emit_flow import emit_flow, FlowEmitError
from .emit_contract import emit_contracts
from .emit_probe import emit_probe_skeleton
from .checklist import check_flow, ChecklistError, scan_placeholders_only
from .dsl_utils import slugify


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
    source_type: str = "auto"


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
    return (
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
