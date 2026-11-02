"""把 JMeter .jmx 工程转为 Atlas DSL YAML（纯 API 回归）。

方案 3.3 场景4：JMeter 42 工程转 DSL，生产巡检。
只解析 HTTPSampler 的 method/path/body，生成 api_call steps。
不自动执行——生成的 YAML 供人审后再用 atlas run 跑。

用法：
    python scripts/import_jmeter.py input.jmx -o output.yaml
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path
import xml.etree.ElementTree as ET
import yaml


def extract_samplers(jmx_path: Path) -> list[dict]:
    """从 JMX 提取 HTTPSampler 节点：method/path/body/headers。"""
    tree = ET.parse(jmx_path)
    root = tree.getroot()
    samplers = []
    for elem in root.iter():
        if not elem.tag.endswith("HTTPSamplerProxy"):
            continue
        name = elem.get("testname", "")
        props = {}
        for prop in elem.iter():
            tag = prop.tag.split("}")[-1]
            if tag == "stringProp":
                key = prop.get("name", "")
                props[key] = (prop.text or "")
            elif tag == "boolProp":
                key = prop.get("name", "")
                props[key] = prop.text == "true"
        method = "POST"
        path = props.get("HTTPSampler.path", "")
        body = props.get("HTTPSampler.postBodyRaw", "") or props.get("Argument.value", "")
        # JMeter 的 method 在 TestElement.gui_class 里不好解析，默认 POST
        # 如果有 HTTPSampler.method 属性用它
        for prop in elem.iter():
            if prop.tag.endswith("stringProp") and prop.get("name") == "HTTPSampler.method":
                method = (prop.text or "POST").upper()
        samplers.append({"name": name, "method": method, "path": path,
                         "body": body[:500] if body else ""})
    return samplers


def to_atlas_dsl(samplers: list[dict], *, flow_id: str = "jmeter-import") -> dict:
    """把 samplers 转成 Atlas DSL 的 api_call steps。"""
    steps = []
    for i, s in enumerate(samplers, 1):
        if not s["path"]:
            continue
        step = {"task": "api_call", "path": s["path"], "method": s["method"],
                "save_as": f"response_{i}", "domain": "jmeter", "intent": s["name"] or f"jmeter_{i}"}
        if s["body"]:
            try:
                step["body"] = json.loads(s["body"])
            except json.JSONDecodeError:
                step["body"] = s["body"]
        steps.append(step)
    return {"id": flow_id, "steps": steps}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("jmx", type=Path, help="JMeter .jmx 文件")
    parser.add_argument("-o", "--output", type=Path, help="输出 YAML 路径")
    parser.add_argument("--flow-id", default="jmeter-import")
    args = parser.parse_args()

    if not args.jmx.exists():
        print(f"JMX 文件不存在：{args.jmx}", file=sys.stderr)
        return 1

    samplers = extract_samplers(args.jmx)
    flow = to_atlas_dsl(samplers, flow_id=args.flow_id)
    yaml_text = yaml.dump(flow, allow_unicode=True, sort_keys=False)

    if args.output:
        args.output.write_text(yaml_text, encoding="utf-8")
        print(f"导入 {len(samplers)} 个采样器 → {args.output}", file=sys.stderr)
    else:
        print(yaml_text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())