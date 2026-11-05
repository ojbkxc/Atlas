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
    """从 JMX 提取 HTTPSampler 节点：method/path/body/headers。

    HeaderManager 在 JMX 里是 sampler 的兄弟/祖先节点（常为全局一份），巡检请求缺
    isc-app 这类业务识别头后端即回 BUG-920——这里收集全部 HeaderManager 的 name/value
    并入每个 sampler，迁移后不丢鉴权头。
    """
    tree = ET.parse(jmx_path)
    root = tree.getroot()
    # 1) 先收集所有 HTTP Header Manager 的键值对（全局共享）
    headers = {}
    for hm in root.iter():
        if not hm.tag.endswith("HeaderManager"):
            continue
        for ep in hm.iter():
            if ep.tag.endswith("elementProp") and ep.get("elementType") == "Header":
                hname = hval = ""
                for prop in ep.iter():
                    tag = prop.tag.split("}")[-1]
                    if tag == "stringProp":
                        if prop.get("name") == "Header.name":
                            hname = prop.text or ""
                        elif prop.get("name") == "Header.value":
                            hval = prop.text or ""
                if hname:
                    headers[hname] = hval
    # 2) 逐 sampler 提取，附上鉴权头
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
        # body 提取修复两个真实缺陷（巡检 JMX 标准形态踩坑）：
        # ① postBodyRaw 是 boolProp（props 里为 True），旧实现
        #    `props.get("postBodyRaw","") or props.get("Argument.value","")` 在真实工程
        #    「Body of a sampler」形态（raw=true，最常见写法）下把 bool 当 body，
        #    随后 [:500] 切片当场 TypeError 崩掉整个迁移。
        # ② 旧实现 [:500] 静默截断：914 字符真实建单报文从第 500 字符腰斩，JSON 结构毁掉，
        #    to_atlas_dsl 的 json.loads 必失败退回截断串——巡检发出残缺报文得假 400，
        #    正撞本文件对占位符警告的同一口径「误判成 Atlas 缺陷」。报文必须完整迁移。
        # 参数按 elementProp 分组结构化解析（props 单键字典会把多参数互相覆盖）：
        # raw 模式取第一个正文 value；参数模式拼 name=value&name=value。
        # 注意跳过 Arguments 容器节点——elem.iter() 会把外层 elementProp 也当参数收集，
        # 它的嵌套迭代取到「最后一个」子项的 name/value，多参数报文会重复拼接一条。
        arguments = []
        for node in elem.iter():
            if node.tag.split("}")[-1] != "elementProp":
                continue
            if any(ch is not node and ch.tag.split("}")[-1] == "elementProp"
                   for ch in node.iter()):
                continue
            a_name = a_value = None
            for child in node.iter():
                if child.tag.split("}")[-1] != "stringProp":
                    continue
                pname = child.get("name", "")
                if pname.endswith("Argument.name"):
                    a_name = child.text or ""
                elif pname.endswith("Argument.value"):
                    a_value = child.text or ""
            if a_value is not None:
                arguments.append((a_name or "", a_value))
        raw_body = props.get("HTTPSampler.postBodyRaw") is True
        if not arguments:
            # 精简/手写 JMX 形态：Argument.value 直接挂 sampler 而无 elementProp 包装
            #（本仓库 MINIMAL_JMX 即此形态）——旧实现靠 props 单键取到，结构化后须兜底，
            # 否则迁移产物静默丢正文。
            legacy = props.get("Argument.value")
            body = legacy if isinstance(legacy, str) else ""
        elif raw_body:
            body = arguments[0][1] if arguments else ""
        elif all(not name for name, _ in arguments):
            # 参数全是无名正文：拼 key=value 会把报文前缀弄脏，直接取 value 原样。
            body = "&".join(v for _, v in arguments)
        else:
            body = "&".join(f"{name}={value}" for name, value in arguments)
        # 如果有 HTTPSampler.method 属性用它
        for prop in elem.iter():
            if prop.tag.endswith("stringProp") and prop.get("name") == "HTTPSampler.method":
                method = (prop.text or "POST").upper()
        samplers.append({"name": name, "method": method, "path": path,
                         "body": body, "headers": dict(headers)})
    return samplers


def unresolved_placeholders(flow: dict) -> list[tuple[int, str, str]]:
    """扫描生成的 DSL，找出仍含 JMeter ${VAR}/${__P(...)} 占位符的字段。

    迁移脚本不改语义：占位符原样保留供人审时手动接 env，但必须点名——否则当字面量发出
    会得到一堆假 400 误判成 Atlas 缺陷。body 常是被 json.loads 过的嵌套结构，需递归下钻。
    返回 (步骤序号, 字段路径, 值)。
    """
    found = []

    def scan(value, where: str, index: int) -> None:
        if isinstance(value, str):
            if "${" in value:
                found.append((index, where, value))
        elif isinstance(value, dict):
            for k, v in value.items():
                scan(v, f"{where}.{k}", index)
        elif isinstance(value, list):
            for pos, v in enumerate(value):
                scan(v, f"{where}[{pos}]", index)

    for index, step in enumerate(flow.get("steps", []), 1):
        for key in ("path", "method"):
            scan(step.get(key), key, index)
        scan(step.get("body"), "body", index)
        for hname, hval in (step.get("headers") or {}).items():
            scan(hval, f"header:{hname}", index)
    return found


def to_atlas_dsl(samplers: list[dict], *, flow_id: str = "jmeter-import") -> dict:
    """把 samplers 转成 Atlas DSL 的 api_call steps。"""
    steps = []
    for i, s in enumerate(samplers, 1):
        if not s["path"]:
            continue
        step = {"task": "api_call", "path": s["path"], "method": s["method"],
                "save_as": f"response_{i}", "domain": "jmeter", "intent": s["name"] or f"jmeter_{i}"}
        if s.get("headers"):
            step["headers"] = dict(s["headers"])
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

    hdr_n = sum(1 for s in flow["steps"] if s.get("headers"))
    placeholders = unresolved_placeholders(flow)
    if placeholders:
        print(f"⚠ {len(placeholders)} 处 JMeter ${'{'}VAR{'}'} 占位符未解析，保留原样待人审接 env：",
              file=sys.stderr)
        for idx, key, val in placeholders[:20]:
            print(f"  步骤{idx} {key} = {val[:80]}", file=sys.stderr)

    if args.output:
        args.output.write_text(yaml_text, encoding="utf-8")
        print(f"导入 {len(samplers)} 个采样器（{hdr_n} 步带鉴权头）→ {args.output}", file=sys.stderr)
    else:
        print(yaml_text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())