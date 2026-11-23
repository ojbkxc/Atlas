"""atlas review-code: 测试代码质量评审。

对 pytest 用例 / DSL flow YAML / 契约定义做质量评审，输出含评分的专业报告
（Allure 2.39.0 风格）。

核心价值：能反向发现"为变绿而放宽断言"的反模式（弱断言、假绿、生产写风险），
呼应红线 #7「禁止为变绿而放宽断言、吞异常、把失败改 SKIP」。

边界（与 atlas review 审核塔区分）：
- review 审运行产物（失败草稿、坑点候选、选择器候选）；review-code 审源码
- review 批准后写知识；review-code 永不写知识、永不批准
- review 的单元是证据卡片（一次运行事件）；review-code 的单元是 finding（一处代码问题）

红线遵守：
- 不放宽断言：只会把"放宽断言"标为问题；自身无任何改断言/改代码路径
- 不写知识：--to-tower 也只造 pending_review 草稿
- 不引运行时依赖：static/knowledge 模式纯 stdlib（ast/json/yaml）
- ai 模式未实现：CLI 拒绝 --review-mode ai（argparse rc 2），不静默降级到 static
"""
from __future__ import annotations
import ast
import hashlib
import json
import re
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

from .atomic_io import read_jsonl_text

# ── 维度权重（默认，可经 reviewcode.json 覆盖）────────────────────────────
DEFAULT_WEIGHTS = {
    "assertion": 0.25,
    "safety": 0.20,
    "maintainability": 0.20,
    "coverage": 0.20,
    "duplication": 0.15,
}

# severity → 扣分（默认，可配置）
SEVERITY_DEDUCTIONS = {"critical": 25, "high": 15, "medium": 8, "low": 3}

# critical 硬顶：出现任一 critical finding 时总分不超过此值
CRITICAL_CAP = 59

# 等级阈值
GRADE_THRESHOLDS = [("A", 90), ("B", 80), ("C", 70), ("D", 60), ("F", 0)]

# 维度 key → 中文名
DIMENSION_NAMES = {
    "assertion": "断言强度",
    "safety": "安全性",
    "maintainability": "可维护性",
    "coverage": "覆盖度",
    "duplication": "重复度",
}

# 规则 id → (category, dimension, severity, rationale, suggestion)
RULES: dict[str, tuple[str, str, str, str, str]] = {
    # ASSERT-* 断言强度
    "ASSERT-001": ("断言强度", "assertion", "critical",
                   "恒真断言：assert 后跟字面量真值，不验证任何业务逻辑",
                   "断言应验证具体业务值，如 assert resp['data']['status'] == 'ACTIVE'"),
    "ASSERT-002": ("断言强度", "assertion", "high",
                   "弱断言：仅判非 None/类型/非空，未验证任何业务字段",
                   "断言具体字段值，如 assert resp['data']['paymentTerms'] == 'NET30'"),
    "ASSERT-003": ("断言强度", "assertion", "medium",
                   "仅断言成功码，未验证响应体内容",
                   "除状态码外，还应断言响应体中的关键业务字段"),
    "ASSERT-004": ("断言强度", "assertion", "critical",
                   "路径恒真假绿：断言操作数是打错的路径，不在任何步骤产出中，恒真通过",
                   "修正路径拼写或补全步骤产出，确保断言操作数引用真实产出"),
    "ASSERT-005": ("断言强度", "assertion", "medium",
                   "非空即过：仅有非空断言，无值相等断言",
                   "补充值相等断言，验证产出与期望值一致"),
    "ASSERT-006": ("断言强度", "assertion", "high",
                   "断言被注释或停用：注释掉的 assert、无 reason 的 xfail/skip",
                   "恢复断言或添加 reason 说明为何跳过"),
    "ASSERT-007": ("断言强度", "assertion", "medium",
                   "用容差掩盖：pytest.approx 或 abs(a-b)<ε 掩盖本应精确相等的值",
                   "对精确值用 == 比较，容差仅用于浮点计算场景"),
    # GREEN-* 假绿/吞异常
    "GREEN-001": ("假绿/吞异常", "assertion", "critical",
                   "吞异常：except 后直接 pass，异常被静默忽略",
                   "记录或重新抛出异常，绝不静默吞掉"),
    # GREEN-001 的**已声明理由**档（2026-11-23）：同行/上一行写了为什么可以吞（如
    # 「关闭失败不得顶掉原始异常」）时不再算 critical——本仓纪律是「吞异常必须有理由」，
    # 而理由在注释里；旧实现一律 critical，`atlas review-code src` 于是把 27 处**有理由的
    # 收尾吞异常**判成 critical，整份报告 grade F、真实信号被噪声淹没。仍然**记一条**（low、
    # 带「需人审确认理由成立」），不放行也不消失。
    "GREEN-001-DECLARED": ("假绿/吞异常", "assertion", "low",
                           "吞异常但已写理由（注释在 except/上一行/pass 行）——需人审确认理由成立",
                           "确认理由成立；否则改为记录日志或重新抛出，绝不静默吞掉"),
    "GREEN-002": ("假绿/吞异常", "assertion", "critical",
                   "断言被 try 包住后继续：try: assert ... except: continue/pass",
                   "移除 try/except 包裹断言，让断言失败正常传播"),
    "GREEN-003": ("假绿/吞异常", "assertion", "critical",
                   "失败改 SKIP：原断言位置新增 pytest.skip 或断言被删",
                   "恢复断言，不得将失败改为跳过"),
    "GREEN-004": ("假绿/吞异常", "assertion", "low",
                   "掩盖告警：type: ignore / noqa / pytest.warns(None) 滥用",
                   "修复底层问题而非掩盖告警"),
    "GREEN-005": ("假绿/吞异常", "assertion", "high",
                   "断言对象是 mock：被断言变量由 mock 固定返回，验证的不是被测物",
                   "断言真实被测对象的行为，而非 mock 的预设返回值"),
    "GREEN-006": ("假绿/吞异常", "assertion", "critical",
                   "断言降级为打印：assert 被改为 print/log，验证消失",
                   "恢复断言，打印不能替代验证"),
    # SAFE-* 生产写/安全
    "SAFE-001": ("生产安全", "safety", "critical",
                   "硬编码凭据/URL：明文密码/token/真实 URL 字面量",
                   "凭据走环境变量或 .env，URL 走 ATLAS_* 配置"),
    # SAFE-001 的**注释档**（2026-11-23）：凭据形态出现在注释里不可执行，但仍可能把真值留在
    # 仓库里，所以**记一条 low** 而不是丢掉；同时不再让说明性注释把整份报告压成 grade F
    # （实测 6/16 个 critical 全是「注释里描述凭据形态」与「README 里的 `<密码>` 占位」）。
    "SAFE-001-COMMENT": ("生产安全", "safety", "low",
                         "注释里出现凭据/URL 形态（不可执行；确认不是把真值留在注释里）",
                         "注释里改用 `<redacted>`/`<密码>` 占位，真值只放 .env"),
    "SAFE-002": ("生产安全", "safety", "critical",
                   "生产写步骤：DSL 写 task 配 prod 环境",
                   "生产环境禁止写操作，测试应在非生产环境执行"),
    "SAFE-003": ("生产安全", "safety", "critical",
                   "无「非人工创建」：建单路径 remark 缺失或为空",
                   "自动创建的单据必须带「非人工创建」备注"),
    "SAFE-004": ("生产安全", "safety", "high",
                   "配额后置/无单号：create 配额在调用后占用，creator 无单号校验",
                   "配额在调用前占用，建单后校验单号存在"),
    "SAFE-005": ("生产安全", "safety", "high",
                   "未验证 API 兜底：DSL channel:auto 用了无显式 API 能力的写操作",
                   "写操作不得依赖未验证 API 兜底，明确指定执行通道"),
    # MAINT-* 可维护性
    "MAINT-001": ("可维护性", "maintainability", "high",
                   "硬 sleep：time.sleep 而非语义等待",
                   "使用 waits.py 语义等待（等待元素出现/消失/值变化）"),
    "MAINT-002": ("可维护性", "maintainability", "medium",
                   "硬编码选择器：选择器字面量不在组件注册表",
                   "选择器应走组件注册表（components.json）"),
    "MAINT-003": ("可维护性", "maintainability", "medium",
                   "写死数据：写死历史单号/客户号，应走 requires/find_*",
                   "数据通过 requires 供给或 find_* 探测，不写死"),
    "MAINT-004": ("可维护性", "maintainability", "medium",
                   "写死环境值：硬编码 URL 路径/环境键，应走 ATLAS_*/.env",
                   "环境值走 ATLAS_* 配置或 .env，不硬编码"),
    "MAINT-005": ("可维护性", "maintainability", "critical",
                   "步骤内数据兜底：换客户/降数量/抄历史单凑过",
                   "不得在步骤内修改数据凑过测试，应修复根因"),
    "MAINT-006": ("可维护性", "maintainability", "high",
                   "DSL 结构坏：缺 task/引用未注册 task/步骤非映射/供给落点冲突",
                   "修复 DSL 结构：确保 task 引用正确、步骤形态合法"),
    # COV-* 覆盖度
    "COV-001": ("覆盖度", "coverage", "high",
                   "有写无验：step 声明了 produces 但 assert 块无消费该产出键的断言",
                   "对每个 produces 产出键，assert 块应有对应的值相等断言"),
    "COV-002": ("覆盖度", "coverage", "medium",
                   "契约缺失/坏：接口调用无契约，或契约空 path/name/形态坏",
                   "补充契约定义或修复坏契约，确保接口有可执行校验"),
    "COV-003": ("覆盖度", "coverage", "medium",
                   "分支未覆盖：状态机 terminal/guard 未在用例中触及",
                   "补充用例覆盖所有 terminal 状态与 guard 分支"),
    "COV-004": ("覆盖度", "coverage", "low",
                   "仅 happy path：无失败/边界/空数据场景",
                   "补充边界用例：失败场景、空数据、边界值"),
    # DUP-* 重复度
    "DUP-001": ("重复度", "duplication", "low",
                   "重复步骤块：连续或多处相同 step 序列",
                   "抽取为子任务或子流复用"),
    "DUP-002": ("重复度", "duplication", "low",
                   "命名不一致：step id 重复/缺失、命名风格混用",
                   "统一命名风格，确保 step id 唯一且有意义"),
    "DUP-003": ("重复度", "duplication", "low",
                   "断言重复：同断言在多处复制而无抽取",
                   "抽取公共断言为辅助函数复用"),
}

# 凭据/URL 正则模式（SAFE-001）
# 字面秘密赋值（password=/token=/…）：**永不豁免**——豁免只可能开在 URL 形态上，
# 真字面 token 无论出现在哪个目录/哪一行都仍 critical（W3 追加项②硬约束：
# 「看着刺眼是对的」，C4 故意放进 tests/ 的字面凭据样本必须继续吃 critical）。
_CREDENTIAL_PATTERNS = [
    re.compile(r"password\s*=\s*['\"][^'\"]{3,}['\"]", re.IGNORECASE),
    re.compile(r"passwd\s*=\s*['\"][^'\"]{3,}['\"]", re.IGNORECASE),
    re.compile(r"token\s*=\s*['\"][^'\"]{8,}['\"]", re.IGNORECASE),
    re.compile(r"api_key\s*=\s*['\"][^'\"]{8,}['\"]", re.IGNORECASE),
    re.compile(r"secret\s*=\s*['\"][^'\"]{8,}['\"]", re.IGNORECASE),
]
_URL_PATTERN = re.compile(r"https?://[^\s'\"]+", re.IGNORECASE)

# SAFE-001 URL 豁免判据（唯一豁免面，W3 追加项②）：**只认保留域**
# （RFC 2606 example 家族 + .test/.invalid，RFC 6761 localhost/回环）。
# 严禁按目录/文件豁免（「tests 目录整体不判」= 静默泄露口，已被否决）。
# host 由 urlsplit().hostname 归一小写取得，大小写混排的 EXAMPLE.COM 同样命中。
_RESERVED_EXACT_HOSTS = frozenset({
    "example", "example.com", "example.net", "example.org",
    "test", "invalid", "localhost", "127.0.0.1", "::1",
})
_RESERVED_HOST_SUFFIXES = (".example", ".example.com", ".example.net",
                           ".example.org", ".test", ".invalid", ".localhost")


def _url_host_reserved(url: str) -> bool:
    """URL 主机是否 RFC 保留域（虚构样本 host）。解析不出 host 一律 False（保守）。

    源码里的 URL 常量常带转义尾（`"https://real.example\\n"`），`\\n` 在源文本中是
    反斜杠+字母两字符、不是空白——正则 token 会把它并进 URL。host 判定按首个反斜杠
    截断：截断只会让保留域**更容易**被识别（放行面不扩大），真实 host 截后仍是真 host。
    """
    try:
        host = urlsplit(url.split("\\", 1)[0]).hostname
    except ValueError:
        return False
    if not host:
        return False
    return host in _RESERVED_EXACT_HOSTS or host.endswith(_RESERVED_HOST_SUFFIXES)


def _safe001_urls_exempt(line: str) -> bool:
    """行内 URL 全为保留域才豁免；混合行（含任一非保留 host）照旧 critical。"""
    urls = _URL_PATTERN.findall(line)
    return bool(urls) and all(_url_host_reserved(u) for u in urls)


# `<密码>` / `${VAR}` 这类**填写说明**占位：先整段抹掉再判 SAFE-001。
# 为什么（2026-11-23 实测）：产物 README 的运维指引写成
# `export ATLAS_FAST_PASSWORD='<密码>'`、`export ATLAS_BASE_URL='https://<host>'`，
# 旧实现把这两行判成**硬编码凭据 critical**（占位符比阈值长，正则照样命中）——文档教用户
# 接 env 的那一行自己吃 critical，噪声盖住真信号。抹段而不是「见 `<` 就整行跳过」：
# 同一行里若还有真值（`password='hunter2'`）必须继续命中原判据。
_PLACEHOLDER_SEGMENT = re.compile(r"<[^<>\n]{1,60}>|\$\{[^}\n]{1,60}\}")


def _strip_placeholder_segments(line: str) -> str:
    """抹掉 `<…>`/`${…}` 占位段（判 SAFE-001 用；`<ph>` 这种也要消失，否则仍越 3 字符阈）。"""
    return _PLACEHOLDER_SEGMENT.sub("", line)

# 选择器模式（MAINT-002）：CSS/XPath 选择器字面量
# uf-7（wave5）：类选择器从 `.app-main/.el-table/.el-button` **精确样例清单**改为族中立
# `\.[\w-]+`——精确清单只认写死的三个名字，`.el-table__fixed-right`/`.el-loading-mask`
# 这类真实变体全部漏判（探针实证）；族里再添新类名也不必回来改正则（与 ui_family 族
# 标签治理同向）。#id/[attr]/xpath/html 的结构面不变；MAINT-002 判定仍在 knowledge
# 模式按注册表比对（本模式只负责把「像选择器的字面量」找全）。
# 常见仓库元数据 dotfile（.env/.git*/.vscode/.idea）不是选择器，负向前瞻排除——
# 这是文档化的小排除清单，不是完备判据（其余 `.hidden` 类字符串仍按选择器处理）。
_SELECTOR_PATTERN = re.compile(
    r"['\"](\.(?!env(?:['\"]|$)|git|github|vscode|idea)[\w-]+|#\w+|\[.*?\]|//\w+|/html)['\"]")

# 写死数据模式（MAINT-003）：历史单号/客户号
_HARDCODED_DATA_PATTERN = re.compile(
    r"['\"]((?:SO|SO-|CUST|PT|DEMO)[-_]?\d{4,})['\"]", re.IGNORECASE)

# 注释掉的断言（ASSERT-006）——**只在「该行确实注释掉了断言代码」时算**：
# `# assert ...` 的本体是断言语句被注释（假绿面：验证看起来做过）。规则文档/讲解注释
# （`# assert True, assert 1 是恒真断言`）同样以 `# assert` 开头，却不是被注释掉的断言——
# 旧实现按行首正则全判 high，实测 review_code.py 自身 13 条 findings 里 8 条是这种
# **文档噪声**（每个规则的 rationale 注释都以 `# assert …` 开头）。
# 判据：`# assert` 后**跟代码形态**（标识符/字面量/调用，且不是「…是…」这类讲解句）才算。
_COMMENTED_ASSERT = re.compile(r"#\s*assert\b")
# 讲解型注释的典型特征——命中其一即按「文档讲解」处理（不是被注释掉的断言代码）：
# ① 含中文讲解词（是/均/这类/都是/就是/说明/形如/写法/口径/或）；
# ② **多个 assert 并列**（`# assert A, assert B`）：真被注释的断言一行只有一条，
#    讲解句为了枚举形态才把多条 assert 连写（旧实现按行首正则全判 high，
#    review_code.py 自身 13 条 findings 里 8 条是这种文档噪声）；
# ③ 冒号后带**解释段**（`# assert len(x) > 0: len() is the call…`——`:` 后跟讲解，
#    真被注释的断言代码不会在尾部加冒号解释）；
# ④ 夹在 AST 术语/伪代码里（`Compare(left=…)` / `pytest.approx(...)` 省略号形态）。
_DOC_COMMENT_HINT = re.compile(r"(是|均|这类|都是|就是|说明|示例|形如|写法|口径|或|is the|are the|wrapping|the call)")


def _is_commented_assert_code(line: str) -> bool:
    """区分「断言被注释」与「注释里讲解断言」——ASSERT-006 的文档噪声档判据。"""
    m = _COMMENTED_ASSERT.search(line)
    if not m:
        return False
    tail = line[m.end():].strip()
    if not tail:
        return False
    # 讲解尾巴（「…是恒真断言」「…说明」）→ 不是被注释的断言代码
    if _DOC_COMMENT_HINT.search(tail):
        return False
    # 多条 assert 连写（`# assert A, assert B` / `# assert A / assert B`）是枚举讲解；
    # `A == B or assert C` 的 or 连接同理（真断言一行只有一条，讲解才需要枚举形态）
    if tail.count("assert") >= 2 or line.count("assert") >= 3:
        return False
    if " assert " in tail and re.search(r"\bor\b|\bnot\b|不", tail):
        return False
    # 冒号后带解释段（`# assert len(x) > 0: len() is the call…`）：讲解写法——
    # 真被注释的断言代码不会在 `:` 后再接一整段英文解释
    if ":" in tail:
        after_colon = tail.split(":", 1)[1].strip()
        if after_colon and len(after_colon.split()) >= 3:
            return False
    # AST/伪代码术语（`Compare(left=...)`、`pytest.approx(...)` 省略号）：讲解写法
    if "..." in tail or "Compare(" in tail or "pytest.approx(" in tail:
        return False
    # 断言代码形态：表达式开头（标识符/引号/数字/括号），排除中文开头（讲解句常以中文开头）
    return bool(re.match(r"^[A-Za-z_\"'(\d-]", tail))

# 类型忽略/noqa（GREEN-004）
_MASK_PATTERN = re.compile(r"#\s*type:\s*ignore|#\s*noqa")


def _make_finding(rule_id: str, file: str, line: int, snippet: str,
                  config: dict) -> dict:
    """根据 rule_id 构造 finding dict（含归因、扣分）。"""
    category, dimension, severity, rationale, suggestion = RULES[rule_id]
    deduction = config.get("severity_deductions", SEVERITY_DEDUCTIONS).get(severity, 0)
    return {
        "rule_id": rule_id,
        "category": category,
        "severity": severity,
        "dimension": dimension,
        "file": file,
        "line": line,
        "snippet": snippet[:200],
        "rationale": rationale,
        "suggestion": suggestion,
        "deduction": deduction,
    }


def _rule_enabled(rule_id: str, config: dict) -> bool:
    """规则开关：config['rules'] 可逐条开关。"""
    rules_config = config.get("rules", {})
    return rules_config.get(rule_id, True)


# ── 配置加载 ──────────────────────────────────────────────────────────────

def load_config(config_path: str | Path | None) -> dict:
    """加载 reviewcode.json 配置；缺省用默认值。

    权重和必须 = 1，否则 ValueError（CLI 接 _user_error rc 2）。
    """
    config = {
        "weights": dict(DEFAULT_WEIGHTS),
        "severity_deductions": dict(SEVERITY_DEDUCTIONS),
        "critical_cap": CRITICAL_CAP,
        "rules": {},
    }
    if config_path:
        path = Path(config_path)
        if not path.exists():
            raise ValueError(f"配置文件不存在：{path}")
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"配置文件 {path} 顶层必须是映射")
        if "weights" in raw:
            weights = raw["weights"]
            if not isinstance(weights, dict):
                raise ValueError("weights 必须是映射")
            config["weights"].update(weights)
        if "severity_deductions" in raw:
            sd = raw["severity_deductions"]
            if isinstance(sd, dict):
                config["severity_deductions"].update(sd)
        if "critical_cap" in raw:
            cap = raw["critical_cap"]
            if isinstance(cap, (int, float)) and not isinstance(cap, bool):
                config["critical_cap"] = int(cap)
        if "rules" in raw:
            rules = raw["rules"]
            if isinstance(rules, dict):
                config["rules"].update(rules)
    # 权重和校验
    total = sum(config["weights"].values())
    if abs(total - 1.0) > 0.001:
        raise ValueError(f"权重和必须为 1，当前为 {total:.4f}（{config['weights']}）")
    return config


# ── 评分 ──────────────────────────────────────────────────────────────────

def score_findings(findings: list[dict], config: dict) -> dict:
    """计分：每维度得分 = max(0, 100 - Σdeduction)，总分 = Σ(weight × score)。

    critical 硬顶：出现任一 critical → total = min(total, critical_cap)。
    """
    weights = config.get("weights", DEFAULT_WEIGHTS)
    cap = config.get("critical_cap", CRITICAL_CAP)

    dimension_findings: dict[str, list[dict]] = {}
    for f in findings:
        dimension_findings.setdefault(f["dimension"], []).append(f)

    dimensions = {}
    total = 0.0
    for dim, weight in weights.items():
        items = dimension_findings.get(dim, [])
        deduction_sum = sum(f["deduction"] for f in items)
        score = max(0, 100 - deduction_sum)
        dimensions[dim] = {"score": score, "weight": weight,
                           "weighted": round(score * weight, 2),
                           "count": len(items)}
        total += score * weight

    total = round(total)
    critical_count = sum(1 for f in findings if f["severity"] == "critical")
    if critical_count > 0:
        total = min(total, cap)

    grade = "F"
    for name, threshold in GRADE_THRESHOLDS:
        if total >= threshold:
            grade = name
            break

    return {
        "total": total,
        "grade": grade,
        "dimensions": dimensions,
        "critical_count": critical_count,
        "finding_count": len(findings),
    }


# ── AST 扫描：pytest 文件 ─────────────────────────────────────────────────

class _PytestAstScanner(ast.NodeVisitor):
    """遍历 pytest 文件 AST，收集 finding。"""

    def __init__(self, file: str, source_lines: list[str], config: dict,
                 *, is_test: bool = True):
        self.file = file
        self.source_lines = source_lines
        self.config = config
        # 是否测试代码（判据在 `_is_test_module`）：只影响「测试纪律类」规则（MAINT-001 硬 sleep）。
        # 默认 True = 不知道时按测试口径报（fail-visible：宁可多报一条，也不静默放过测试里的硬等待）。
        self.is_test = is_test
        self.findings: list[dict] = []
        self._assert_count = 0

    def _add(self, rule_id: str, line: int, snippet: str):
        if _rule_enabled(rule_id, self.config):
            self.findings.append(_make_finding(rule_id, self.file, line, snippet, self.config))

    def visit_Assert(self, node):
        self._assert_count += 1
        snippet = self.source_lines[node.lineno - 1].strip() if node.lineno <= len(self.source_lines) else ""
        # ASSERT-001: 恒真断言
        if isinstance(node.test, ast.Constant):
            val = node.test.value
            if val is True or val == 1 or val == "x" or (isinstance(val, (list, dict)) and len(val) > 0) or val:
                # assert True, assert 1, assert "x", assert [1,2]
                if val is True or (isinstance(val, (int, float, str, list, dict)) and val):
                    if not (isinstance(val, str) and not val):
                        self._add("ASSERT-001", node.lineno, snippet)
                        return
        # ASSERT-002: 弱断言
        self._check_weak_assert(node, snippet)
        # ASSERT-003: 仅成功码
        self._check_success_code_only(node, snippet)
        # ASSERT-007: 容差掩盖
        self._check_approx(node, snippet)
        self.generic_visit(node)

    def _check_weak_assert(self, node, snippet):
        """检测 assert x is not None / isinstance / len(x)>0 / == True。"""
        test = node.test
        # assert x is not None / assert x is None
        if isinstance(test, ast.Compare) and len(test.ops) == 1:
            op = test.ops[0]
            if isinstance(op, ast.IsNot) and any(
                isinstance(c, ast.Constant) and c.value is None for c in test.comparators):
                self._add("ASSERT-002", node.lineno, snippet)
                return
            if isinstance(op, ast.Eq) and any(
                isinstance(c, ast.Constant) and c.value is True for c in test.comparators):
                self._add("ASSERT-002", node.lineno, snippet)
                return
        # assert isinstance(x, T)  # 形态示例（讲解，非被注释的断言代码）
        if isinstance(test, ast.Call):
            func = test.func
            if isinstance(func, ast.Name) and func.id == "isinstance":
                self._add("ASSERT-002", node.lineno, snippet)
                return
            # assert len(x) > 0  # 形态示例（讲解，非被注释的断言代码）
            if isinstance(func, ast.Name) and func.id == "len":
                if test.args and isinstance(test.args[0], ast.Compare):
                    self._add("ASSERT-002", node.lineno, snippet)
                    return
                # assert len(x) > 0: len() is the call, > 0 is the comparison wrapping it
                # Actually len(x) > 0 would be Compare(left=Call(len), ...) not Call directly
            # assert pytest.approx(...) or abs(a-b) < epsilon
            if isinstance(func, ast.Attribute) and func.attr == "approx":
                self._add("ASSERT-007", node.lineno, snippet)
                return
        # assert len(x) > 0: Compare(left=Call(len), ops=[Gt], comparators=[0])
        if isinstance(test, ast.Compare) and isinstance(test.left, ast.Call):
            left_func = test.left.func
            if isinstance(left_func, ast.Name) and left_func.id == "len":
                self._add("ASSERT-002", node.lineno, snippet)
                return

    def _check_success_code_only(self, node, snippet):
        """检测 assert resp.status_code == 200 / assert r.status_code == 200。"""
        test = node.test
        if isinstance(test, ast.Compare) and len(test.ops) == 1:
            op = test.ops[0]
            if isinstance(op, ast.Eq):
                comparators = test.comparators
                if len(comparators) == 1:
                    c = comparators[0]
                    if isinstance(c, ast.Constant) and c.value == 200:
                        # Check if left side is status_code
                        left = test.left
                        if (isinstance(left, ast.Attribute) and "status_code" in (left.attr or "").lower()
                                or isinstance(left, ast.Attribute) and left.attr == "status_code"):
                            self._add("ASSERT-003", node.lineno, snippet)

    def _check_approx(self, node, snippet):
        """检测 pytest.approx / abs(a-b) < epsilon。"""
        test = node.test
        # assert x == pytest.approx(y) or assert x == approx(y)
        if isinstance(test, ast.Compare) and len(test.comparators) == 1:
            comp = test.comparators[0]
            if isinstance(comp, ast.Call):
                func = comp.func
                if (isinstance(func, ast.Attribute) and func.attr == "approx") or \
                   (isinstance(func, ast.Name) and func.id == "approx"):
                    self._add("ASSERT-007", node.lineno, snippet)
        # assert abs(a-b) < 0.01  # 形态示例（讲解，非被注释的断言代码）
        if isinstance(test, ast.Compare) and isinstance(test.left, ast.Call):
            left_func = test.left.func
            if isinstance(left_func, ast.Name) and left_func.id == "abs":
                self._add("ASSERT-007", node.lineno, snippet)

    def _declared_swallow_reason(self, node) -> bool:
        """吞异常处是否**写了理由**（同行 / 上一行 / body 首行注释，≥6 个非空字符）。

        判据刻意保守：只认注释文本长度，不去猜理由「成不成立」——成立与否是人审的事，
        机器只负责把「有理由」与「裸吞」分开（否则 27 处收尾吞异常把整份报告压成 F，
        真缺陷反而看不出来）。理由文本不进 finding（避免把业务注释抄进报告）。
        """
        candidates = []
        for line_no in (node.lineno - 1, node.lineno - 2):
            if 0 <= line_no < len(self.source_lines):
                candidates.append(self.source_lines[line_no])
        for stmt in node.body:
            line_no = getattr(stmt, "lineno", None)
            if line_no and 0 <= line_no - 1 < len(self.source_lines):
                candidates.append(self.source_lines[line_no - 1])
        for text in candidates:
            comment = text.split("#", 1)[1].strip() if "#" in text else ""
            if len(comment) >= 6:
                return True
        return False

    def visit_ExceptHandler(self, node):
        """GREEN-001: except: pass / except Exception: pass（写了理由的降为已声明档）。"""
        snippet = self.source_lines[node.lineno - 1].strip() if node.lineno <= len(self.source_lines) else ""
        # Check if body is just Pass or Continue
        if node.body and len(node.body) <= 2:
            all_pass = all(isinstance(stmt, ast.Pass) for stmt in node.body)
            has_pass = any(isinstance(stmt, ast.Pass) for stmt in node.body)
            has_continue = any(isinstance(stmt, ast.Continue) for stmt in node.body)
            if (all_pass or (has_pass and not has_continue) or
                    (len(node.body) == 1 and isinstance(node.body[0], (ast.Pass, ast.Continue)))):
                # Bare except or except Exception with pass
                if node.type is None or (isinstance(node.type, ast.Name) and
                                          node.type.id in ("Exception", "BaseException")):
                    rule = ("GREEN-001-DECLARED" if self._declared_swallow_reason(node)
                            else "GREEN-001")
                    self._add(rule, node.lineno, snippet)
        self.generic_visit(node)

    def visit_Try(self, node):
        """GREEN-002: try: assert ... except: continue/pass。"""
        # Check if body contains an Assert
        has_assert = any(isinstance(stmt, ast.Assert) for stmt in node.body)
        if has_assert:
            for handler in node.handlers:
                if handler.body and len(handler.body) <= 2:
                    has_pass = any(isinstance(stmt, ast.Pass) for stmt in handler.body)
                    has_continue = any(isinstance(stmt, ast.Continue) for stmt in handler.body)
                    if has_pass or has_continue:
                        line = handler.lineno
                        snippet = self.source_lines[line - 1].strip() if line <= len(self.source_lines) else ""
                        self._add("GREEN-002", line, snippet)
        self.generic_visit(node)

    def visit_Call(self, node):
        """GREEN-003: pytest.skip(...) in test function.
        GREEN-005: assert with mock object.
        MAINT-001: time.sleep(...).
        """
        snippet = self.source_lines[node.lineno - 1].strip() if node.lineno <= len(self.source_lines) else ""
        func = node.func
        # pytest.skip(...) → GREEN-003 (if in test context)
        if isinstance(func, ast.Attribute):
            attr = func.attr
            # time.sleep → MAINT-001 **仅测试代码**（引擎的轮询实现本体不该被要求「改用 waits.py」）
            if attr == "sleep" and isinstance(func.value, ast.Name) and func.value.id == "time":
                if self.is_test:
                    self._add("MAINT-001", node.lineno, snippet)
            # pytest.skip / pytest.xfail
            if attr == "skip" and isinstance(func.value, ast.Name) and func.value.id == "pytest":
                self._add("GREEN-003", node.lineno, snippet)
        elif isinstance(func, ast.Name):
            if func.id == "sleep":
                # bare sleep(...) call（同样只在测试代码里算硬等待）
                if self.is_test:
                    self._add("MAINT-001", node.lineno, snippet)
        self.generic_visit(node)

    def visit_FunctionDef(self, node):
        """检查装饰器：xfail/skip 无 reason → ASSERT-006。"""
        for deco in node.decorator_list:
            if isinstance(deco, ast.Call):
                func = deco.func
                deco_name = ""
                if isinstance(func, ast.Attribute):
                    deco_name = func.attr
                elif isinstance(func, ast.Name):
                    deco_name = func.id
                if deco_name in ("xfail", "skip"):
                    # Check if reason is provided
                    has_reason = False
                    for kw in deco.keywords:
                        if kw.arg == "reason" and kw.value is not None:
                            has_reason = True
                            break
                    if not has_reason:
                        snippet = self.source_lines[node.lineno - 1].strip() if node.lineno <= len(self.source_lines) else ""
                        self._add("ASSERT-006", node.lineno, snippet)
        self.generic_visit(node)


def _docstring_lines(tree: ast.AST) -> set[int]:
    """模块/类/函数 **docstring** 占用的行号集合（1-based，含多行区间）。

    为什么要单列（2026-11-23 实测）：`config.py` 的模块 docstring 里写「代理形态
    `http://user:pass@host:port`」，行级扫描把它当代码判成 SAFE-001 **critical**。
    docstring 与注释同属**文档面**：不可执行、且这里出现的正是「教用户怎么填」的说明，
    应与注释同档（`SAFE-001-COMMENT`，low、留痕），不能让说明文字压掉真信号。
    """
    lines: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", [])
        if not body:
            continue
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
                and isinstance(first.value.value, str):
            start = getattr(first, "lineno", None)
            end = getattr(first, "end_lineno", start)
            if start:
                lines.update(range(start, (end or start) + 1))
    return lines


def _scan_pytest_line_level(file: str, source: str, config: dict,
                            findings: list[dict], *,
                            doc_lines: set[int] | None = None) -> None:
    """行级扫描：注释掉的断言、type: ignore/noqa、硬编码凭据/选择器/数据。"""
    lines = source.splitlines()
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        # ASSERT-006: 注释掉的断言（讲解型注释不算——`# assert x is None` 若是规则文档
        # 的一部分，判 high 会把评审器自己的 rationale 全部打成噪声）
        if _COMMENTED_ASSERT.search(line) and _is_commented_assert_code(line):
            if _rule_enabled("ASSERT-006", config):
                findings.append(_make_finding("ASSERT-006", file, i, stripped, config))
        # GREEN-004: type: ignore / noqa
        if _MASK_PATTERN.search(line):
            if _rule_enabled("GREEN-004", config):
                findings.append(_make_finding("GREEN-004", file, i, stripped, config))
        # SAFE-001: 硬编码凭据/URL
        # 顺序敏感：字面秘密赋值恒判；只有「仅 URL 命中且 host 全为保留域」才放行
        # （豁免只此一面，见 _safe001_urls_exempt 注释）。
        # 两处 2026-11-23 收紧（都是**减噪声不减判据**）：
        # ①`<…>`/`${…}` 填写说明占位先抹段（产物 README 的 `'<密码>'` 不是硬编码）；
        # ②注释行降 `SAFE-001-COMMENT`（low）：不可执行，但仍留痕（别把真值留注释里）。
        probe_line = _strip_placeholder_segments(line)
        is_comment = stripped.startswith("#") or i in (doc_lines or set())
        credential_hit = any(p.search(probe_line) for p in _CREDENTIAL_PATTERNS)
        url_hit = bool(_URL_PATTERN.search(probe_line)) and not _safe001_urls_exempt(probe_line)
        if credential_hit or url_hit:
            rule = "SAFE-001-COMMENT" if is_comment else "SAFE-001"
            if _rule_enabled(rule, config):
                findings.append(_make_finding(rule, file, i, stripped, config))
        # MAINT-002: 硬编码选择器（不在注册表中检测在 knowledge 模式做，这里只标记字面量）
        # MAINT-003: 写死数据
        if _HARDCODED_DATA_PATTERN.search(line):
            if _rule_enabled("MAINT-003", config):
                findings.append(_make_finding("MAINT-003", file, i, stripped, config))


def scan_pytest_file(path: Path, config: dict,
                     registry: Any = None) -> list[dict]:
    """扫描单个 pytest 文件，返回 finding 列表。

    registry: 可选的 SelectorRegistry（knowledge 模式用，检查选择器是否注册）。
    """
    # 读侧 replace（#131 纪律，W3 追加项①）：坏字节文件不再把整份评审顶成
    # UnicodeDecodeError——语法解析失败落 MAINT-006 finding，评审继续扫其余目标。
    source = path.read_text(encoding="utf-8", errors="replace")
    file_str = str(path)
    findings: list[dict] = []

    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        # 语法错误本身是 MAINT-006 级问题
        if _rule_enabled("MAINT-006", config):
            findings.append(_make_finding("MAINT-006", file_str, 1,
                                          "syntax error", config))
        return findings

    scanner = _PytestAstScanner(file_str, source.splitlines(), config,
                                is_test=_is_test_module(path))
    scanner.visit(tree)
    findings.extend(scanner.findings)

    # 行级扫描（docstring 行号一并给：文档面与注释同档，见 `_docstring_lines`）
    _scan_pytest_line_level(file_str, source, config, findings,
                            doc_lines=_docstring_lines(tree))

    # knowledge 模式：检查硬编码选择器是否在注册表
    if registry is not None:
        for i, line in enumerate(source.splitlines(), 1):
            for match in _SELECTOR_PATTERN.finditer(line):
                selector = match.group(1)
                # 简化检查：如果选择器不在注册表中任何组件，标记
                if not _selector_in_registry(selector, registry):
                    if _rule_enabled("MAINT-002", config):
                        findings.append(_make_finding(
                            "MAINT-002", file_str, i, line.strip(), config))

    # knowledge 模式：检查 import 的 API 调用是否有契约
    # (简化：检查是否有 api_call/call_endpoint 调用但无对应契约)
    if registry is not None:
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Attribute) and func.attr in ("get", "post", "put", "delete"):
                    # 检查是否有契约（简化：跳过，契约检查在 DSL 侧做）
                    pass

    return findings


def _selector_in_registry(selector: str, registry: Any) -> bool:
    """检查选择器是否在任何组件的注册表中。"""
    try:
        components = registry.components
        for spec in components.values():
            selectors = spec.get("selectors", {})
            if isinstance(selectors, dict) and selector in selectors.values():
                return True
    except Exception:  # 注册表读坏=判不了，按「未注册」返回 False（本函数只服务 MAINT-002 提示）
        pass
    return False


# ── DSL flow YAML 扫描 ────────────────────────────────────────────────────

def scan_dsl_flow(path: Path, flow: dict, config: dict,
                  registry: Any = None) -> list[dict]:
    """扫描 DSL flow YAML，返回 finding 列表。

    复用 dsl.FlowCompiler.validate_flow 做结构校验。
    """
    file_str = str(path)
    findings: list[dict] = []

    # 结构校验（MAINT-006）
    if registry is not None:
        try:
            from .dsl import FlowCompiler
            FlowCompiler(registry).validate_flow(flow)
        except (ValueError, KeyError, RuntimeError) as exc:
            if _rule_enabled("MAINT-006", config):
                findings.append(_make_finding(
                    "MAINT-006", file_str, 1, str(exc), config))

    steps = flow.get("steps", [])
    asserts = flow.get("assert", [])

    # 收集所有 produces 键
    produces_keys: set[str] = set()
    for step in steps:
        if isinstance(step, dict):
            # produces 在 TaskMetadata 中，DSL 层面通过 task 的 produces 声明
            # 简化：检查 step 中是否有 produces 声明
            prod = step.get("produces")
            if isinstance(prod, (list, tuple)):
                produces_keys.update(str(p) for p in prod)
            elif isinstance(prod, str):
                produces_keys.add(prod)

    # COV-001: 有写无验
    if produces_keys and asserts:
        assert_texts = []
        for a in asserts:
            if isinstance(a, str):
                assert_texts.append(a)
            elif isinstance(a, dict):
                assert_texts.extend(str(v) for v in a.values())
        # 检查是否有 assert 消费 produces 键
        consumed = False
        for key in produces_keys:
            for text in assert_texts:
                if key in text:
                    consumed = True
                    break
            if consumed:
                break
        if not consumed and _rule_enabled("COV-001", config):
            findings.append(_make_finding(
                "COV-001", file_str, 1,
                f"produces keys {sorted(produces_keys)} not consumed in asserts",
                config))

    # ASSERT-004/005: DSL assert 块检查
    _non_empty_re = re.compile(
        r"^\s*(?:\$\{?)?\w+\}?\s*!=\s*['\"]?['\"]?\s*$")
    _path_re = re.compile(r"^(?:\$\{?)?([A-Za-z_]\w*(?:\.\w+)+)\}?$")
    for i, item in enumerate(asserts, 1):
        if isinstance(item, str):
            # ASSERT-005: 非空即过 — document_no != "" 或 ${document_no} != ""
            if _non_empty_re.match(item):
                if _rule_enabled("ASSERT-005", config):
                    findings.append(_make_finding(
                        "ASSERT-005", file_str, i, item, config))
            # ASSERT-004: 路径恒真（两个打错的路径）
            m = re.match(r"^(.+?)\s*(==|!=)\s*(.+)$", item)
            if m:
                left, right = m.group(1).strip(), m.group(3).strip()
                left_path = _path_re.match(left)
                right_path = _path_re.match(right)
                if left_path and right_path:
                    lp = left_path.group(1)
                    rp = right_path.group(1)
                    # 检查是否都不在 produces 中
                    if lp not in produces_keys and rp not in produces_keys:
                        if _rule_enabled("ASSERT-004", config):
                            findings.append(_make_finding(
                                "ASSERT-004", file_str, i, item, config))
        elif isinstance(item, dict):
            for key, val in item.items():
                if isinstance(val, str) and _non_empty_re.match(val):
                    if _rule_enabled("ASSERT-005", config):
                        findings.append(_make_finding(
                            "ASSERT-005", file_str, i, f"{key}: {val}", config))

    # SAFE-002: 生产写步骤
    engine_env = flow.get("engine", {}).get("env", "") if isinstance(flow.get("engine"), dict) else ""
    for i, step in enumerate(steps, 1):
        if not isinstance(step, dict):
            continue
        task_name = step.get("task", "")
        read_only = step.get("read_only", True)
        # 写操作：read_only: false 或 task 名暗示写
        is_write = not read_only or any(
            kw in str(task_name).lower()
            for kw in ("create", "save", "submit", "delete", "update"))
        if is_write and "prod" in str(engine_env):
            if _rule_enabled("SAFE-002", config):
                findings.append(_make_finding(
                    "SAFE-002", file_str, i,
                    f"task={task_name} env={engine_env}", config))

    # SAFE-005: channel:auto 的写操作
    for i, step in enumerate(steps, 1):
        if isinstance(step, dict) and step.get("channel") == "auto":
            task_name = str(step.get("task", "")).lower()
            if any(kw in task_name for kw in ("create", "save", "submit", "delete")):
                if _rule_enabled("SAFE-005", config):
                    findings.append(_make_finding(
                        "SAFE-005", file_str, i,
                        f"task={step.get('task')} channel=auto", config))

    return findings


# ── 契约 JSON 扫描 ────────────────────────────────────────────────────────

def scan_contract_file(path: Path, config: dict) -> list[dict]:
    """扫描契约 JSON 文件，返回 finding 列表。

    复用 contracts.ContractRegistry.load 的判据。
    """
    file_str = str(path)
    findings: list[dict] = []

    try:
        from .contracts import ContractRegistry
        registry = ContractRegistry.load(path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        if _rule_enabled("COV-002", config):
            findings.append(_make_finding(
                "COV-002", file_str, 1, str(exc), config))
        return findings

    # 检查空 path/name
    for method, cpath in sorted(registry.contracts):
        contract = registry.contracts[(method, cpath)]
        if not contract.path:
            if _rule_enabled("COV-002", config):
                findings.append(_make_finding(
                    "COV-002", file_str, 1,
                    f"{contract.name}: path 为空", config))
        if not contract.name:
            if _rule_enabled("COV-002", config):
                findings.append(_make_finding(
                    "COV-002", file_str, 1,
                    f"{method} {cpath}: name 为空", config))

    return findings


# ── 目标识别与扫描分发 ────────────────────────────────────────────────────

def _is_test_module(path: Path) -> bool:
    """该 .py 是不是**测试代码**（`MAINT-001` 的适用面）。

    为什么要分开（2026-11-23 实测）：`MAINT-001`（硬 sleep，high）的建议是「改用 waits.py 语义
    等待」——那是**测试代码**该遵守的纪律。但 `detect_kind` 把所有 `.py` 都当 `pytest` 扫，于是
    引擎自己的轮询实现被判 high：`atomic_io` 的 `time.sleep(_READ_RETRY_DELAY_S)`（读重试退避）、
    `dsl` 的 `time.sleep(interval_ms / 1000.0)`（`await: poll` 的间隔）、`explore` 的命令轮询、
    `legacy_fast` 的等待垫片——这几个正是**语义等待的实现本体**，报「请改用 waits.py」等于要求
    引擎不要实现等待（自指噪声，与 ASSERT-006 那波同类）。
    判据取三条并列（文件名前缀/后缀/路径段），**不**用「含 test 子串」（`contrast.py` 会误中）。
    """
    if path.stem.startswith("test_") or path.stem.endswith("_test"):
        return True
    return any(part == "tests" for part in path.parts)


def detect_kind(path: Path) -> str:
    """按扩展名/内容识别目标类型。"""
    suffix = path.suffix.lower()
    if suffix == ".py":
        return "pytest"
    if suffix in (".yaml", ".yml"):
        return "dsl"
    if suffix == ".json":
        return "contract"
    # 内容嗅探
    try:
        text = path.read_text(encoding="utf-8", errors="replace")[:500]
        if "steps" in text or "assert" in text:
            return "dsl"
        if "contracts" in text:
            return "contract"
        if "def test_" in text:
            return "pytest"
    except OSError:
        pass
    return "pytest"


_SCANNABLE_SUFFIXES = frozenset({".py", ".yaml", ".yml", ".json"})


def _iter_targets(paths: list[str | Path], kind: str) -> list[tuple[Path, str]]:
    """展开路径列表为 (file, kind) 列表。

    目录递归只收可评审文本形态（.py/.yaml/.yml/.json）——旧实现 rglob("*") 把
    `__pycache__/*.pyc` 也收进来，`scan_pytest_file` 的严格 utf-8 读当场裸崩
    UnicodeDecodeError（`atlas review-code tests/` 必踩的预存红灯，W3 追加项①）。
    显式传入的单文件不受扩展名过滤影响（用户点名要审什么就审什么）。
    """
    results = []
    for p in paths:
        path = Path(p)
        if path.is_dir():
            for child in sorted(path.rglob("*")):
                if child.is_file() and child.suffix.lower() in _SCANNABLE_SUFFIXES:
                    k = kind if kind != "auto" else detect_kind(child)
                    results.append((child, k))
        elif path.is_file():
            k = kind if kind != "auto" else detect_kind(path)
            results.append((path, k))
    return results


# ── 基线差分与运行史交叉 ──────────────────────────────────────────────────

def _extract_assert_info(source: str) -> list[str]:
    """从源码提取 assert 表达式文本（用于差分）。"""
    asserts = []
    try:
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assert):
                try:
                    asserts.append(ast.unparse(node.test))
                except Exception:  # 单个断言 unparse 不了就跳过（源码里已有 SyntaxError 兜底，逐条降级）
                    pass
    except SyntaxError:
        pass
    return asserts


def diff_baseline(current_source: str, baseline_source: str,
                  file: str, config: dict) -> list[dict]:
    """基线差分：对比当前版与基线版的断言。

    检测：断言被删除/注释、assert → skip/print、断言被弱化。
    """
    findings = []
    current_asserts = _extract_assert_info(current_source)
    baseline_asserts = _extract_assert_info(baseline_source)

    # 断言计数下降
    if len(current_asserts) < len(baseline_asserts):
        if _rule_enabled("GREEN-003", config):
            findings.append(_make_finding(
                "GREEN-003", file, 1,
                f"断言数从 {len(baseline_asserts)} 降为 {len(current_asserts)}",
                config))

    # 断言被弱化：基线有 == 比较，当前只有 != "" 或 is not None
    for base_assert in baseline_asserts:
        if "==" in base_assert and any(
            isinstance(node, ast.Compare) and any(isinstance(op, ast.Eq) for op in node.ops)
            for node in ast.walk(ast.parse(base_assert))
        ):
            # 检查当前版是否还有类似的 == 比较
            found_equivalent = False
            for cur_assert in current_asserts:
                if "==" in cur_assert:
                    found_equivalent = True
                    break
            if not found_equivalent and current_asserts:
                # 当前版只有弱断言
                if _rule_enabled("GREEN-003", config):
                    findings.append(_make_finding(
                        "GREEN-003", file, 1,
                        f"基线断言 {base_assert[:80]} 被弱化",
                        config))

    return findings


def cross_check_runs(runs_path: Path, findings: list[dict],
                     config: dict) -> list[dict]:
    """运行史交叉：读 runs.jsonl，找曾 failed(assertion) 现 passed 的 case。

    结合当前 findings 判断是否"疑似为变绿放宽断言"。
    """
    extra_findings = []
    if not runs_path.exists():
        return extra_findings

    # cc-2 读侧配套：runs.jsonl 写锁区间=文件头 1 字节，Windows 并发读撞锁 PermissionError
    # （瞬态）由 read_jsonl_text 内部重试即过；判据与 atomic_io 同一把尺（#92）：只重试
    # PermissionError，UnicodeDecodeError 直接回落 replace，其余异常原样传播。
    # BOM 口径同单点：带 BOM 的 runs.jsonl 首行 past 会被 json.loads 拒成残行而静默丢失。
    raw = read_jsonl_text(runs_path)

    failed_assertion_cases = set()
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            run = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(run, dict):
            continue
        if run.get("status") == "failed":
            for step in run.get("steps", []):
                if not isinstance(step, dict):
                    continue
                fc = step.get("fail_class", "")
                if fc == "assertion":
                    case = run.get("case", "")
                    if case:
                        failed_assertion_cases.add(case)

    if not failed_assertion_cases:
        return extra_findings

    # 检查当前 findings 中是否有弱断言/假绿相关 finding
    has_weakening = any(
        f["rule_id"] in ("ASSERT-001", "ASSERT-002", "GREEN-001", "GREEN-002",
                          "GREEN-003", "GREEN-006", "MAINT-005")
        for f in findings
    )
    if has_weakening:
        for case in failed_assertion_cases:
            if _rule_enabled("GREEN-003", config):
                extra_findings.append(_make_finding(
                    "GREEN-003", str(runs_path), 1,
                    f"case {case} 曾 failed(assertion) 现 passed 且断言被弱化",
                    config))

    return extra_findings


# ── 报告生成 ──────────────────────────────────────────────────────────────

def format_json(result: dict) -> str:
    """机器可读 JSON 报告。"""
    return json.dumps(result, ensure_ascii=False, indent=2)


def format_text(result: dict) -> str:
    """人读摘要报告。"""
    summary = result.get("summary", {})
    total = summary.get("avg_score", 100)
    grade = summary.get("grade", "F")
    mode = summary.get("mode", result.get("mode", "static"))
    lines = []
    lines.append(f"=== Atlas 代码质量评审报告 ===")
    lines.append(f"总分: {total}/100  等级: {grade}  "
                 f"模式: {mode}")
    lines.append(f"文件数: {summary.get('files', 0)}  "
                 f"问题数: {summary.get('total_findings', 0)}  "
                 f"critical: {summary.get('critical_findings', 0)}")
    lines.append("")

    # 聚合维度分
    dim_totals: dict[str, dict] = {}
    for fr in result.get("results", []):
        dims = fr.get("score", {}).get("dimensions", {})
        for dim, info in dims.items():
            if dim not in dim_totals:
                dim_totals[dim] = {"score_sum": 0, "weight": info["weight"],
                                   "count": 0}
            dim_totals[dim]["score_sum"] += info["score"]
            dim_totals[dim]["count"] += info["count"]

    if dim_totals:
        lines.append("维度得分（平均）:")
        for dim, info in dim_totals.items():
            name = DIMENSION_NAMES.get(dim, dim)
            avg = round(info["score_sum"] / len(result.get("results", [{}])))
            lines.append(f"  {name} ({dim}): {avg}/100 "
                         f"权重 {info['weight']} × {info['count']} 问题")
        lines.append("")

    findings = result.get("findings", [])
    if findings:
        lines.append("问题清单:")
        for f in findings:
            lines.append(f"  [{f['severity']}] {f['rule_id']} {f['file']}:{f['line']}")
            lines.append(f"    {f['rationale']}")
            lines.append(f"    建议: {f['suggestion']}")
        lines.append("")

    if result.get("baseline_notes"):
        lines.append("基线差分:")
        for note in result["baseline_notes"]:
            lines.append(f"  {note}")
        lines.append("")

    return "\n".join(lines)


def _build_markdown_report(result: dict) -> str:
    """构建 Markdown 附件内容。"""
    score = result.get("score", {})
    total = score.get("total", 0)
    grade = score.get("grade", "F")
    findings = result.get("findings", [])
    mode = result.get("mode", "static")
    lines = []
    lines.append(f"# Atlas 代码质量评审报告")
    lines.append(f"")
    lines.append(f"**总分**: {total}/100  **等级**: {grade}  "
                 f"**模式**: {mode}")
    lines.append(f"**问题数**: {len(findings)}  "
                 f"**critical**: {score.get('critical_count', 0)}")
    lines.append(f"")

    lines.append("## 维度得分")
    lines.append("")
    lines.append("| 维度 | 得分 | 权重 | 问题数 |")
    lines.append("|------|------|------|--------|")
    for dim, info in score.get("dimensions", {}).items():
        name = DIMENSION_NAMES.get(dim, dim)
        lines.append(f"| {name} | {info['score']} | {info['weight']} | {info['count']} |")
    lines.append("")

    if findings:
        lines.append("## 问题清单")
        lines.append("")
        for f in findings:
            lines.append(f"### [{f['severity']}] {f['rule_id']} — {f['category']}")
            lines.append(f"- **文件**: `{f['file']}:{f['line']}`")
            lines.append(f"- **代码**: `{f['snippet']}`")
            lines.append(f"- **归因**: {f['rationale']}")
            lines.append(f"- **建议**: {f['suggestion']}")
            lines.append(f"- **扣分**: -{f['deduction']}")
            lines.append("")

    lines.append("## 改进建议")
    lines.append("")
    if not findings:
        lines.append("未发现问题，继续保持。")
    else:
        for f in findings:
            lines.append(f"- `{f['file']}:{f['line']}` — {f['suggestion']}")
    lines.append("")

    return "\n".join(lines)


def emit_allure_review(file_results: list[dict], out_dir: Path,
                       clean: bool = True) -> dict:
    """把评审结果输出为 Allure 2.39.0 结果集（复用 Gap5 格式）。

    每个被评审文件 = 一个 Allure result；每个维度 = 一个 step；
    每条 finding = 维度 step 下的子 step。
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # 清理旧 Atlas 文件
    if clean:
        for path in out_dir.iterdir():
            if path.is_file() and path.name.startswith("atlas-"):
                try:
                    path.unlink()
                except OSError:
                    pass

    results_count = 0
    for fr in file_results:
        uid = uuid.uuid4().hex
        f = fr.get("file", "unknown")
        score = fr.get("score", {})
        total = score.get("total", 0)
        grade = score.get("grade", "F")
        findings = fr.get("findings", [])

        # 最高 severity
        max_severity = "normal"
        for finding in findings:
            sev = finding["severity"]
            if sev == "critical":
                max_severity = "critical"
                break
            if sev == "high" and max_severity != "critical":
                max_severity = "high"
            elif sev == "medium" and max_severity in ("normal", "low"):
                max_severity = "medium"
            elif sev == "low" and max_severity == "normal":
                max_severity = "low"

        # 状态：有 critical/high → failed，否则 passed
        status = "failed" if max_severity in ("critical", "high") else "passed"

        status_details = _details(
            f"{fr.get('finding_count', 0)} findings, score {total}/100",
            f"file: {f}")

        # 维度 steps
        dimensions = score.get("dimensions", {})
        steps = []
        for dim, info in dimensions.items():
            dim_name = DIMENSION_NAMES.get(dim, dim)
            dim_findings = [fd for fd in findings if fd["dimension"] == dim]
            dim_status = "failed" if dim_findings and any(
                fd["severity"] in ("critical", "high") for fd in dim_findings) else "passed"
            sub_steps = []
            for fd in dim_findings:
                sub_steps.append({
                    "name": f"{fd['rule_id']} L{fd['line']}",
                    "status": "failed",
                    "stage": "finished",
                    "start": 0,
                    "stop": 0,
                    "steps": [],
                    "attachments": [],
                    "parameters": [],
                    "statusDetails": _details(
                        f"{fd['rationale']}",
                        f"{fd['file']}:{fd['line']} — {fd['snippet']}"),
                    "labels": [
                        {"name": "severity", "value": fd["severity"]},
                        {"name": "atlas.rule_id", "value": fd["rule_id"]},
                        {"name": "atlas.category", "value": fd["category"]},
                    ],
                })
            steps.append({
                "name": f"{dim_name} {info['score']}/100",
                "status": dim_status,
                "stage": "finished",
                "start": 0,
                "stop": 0,
                "steps": sub_steps,
                "attachments": [],
                "parameters": [],
                "statusDetails": _details(""),
                "labels": [],
            })

        # Markdown 附件
        md_content = _build_markdown_report(fr)
        md_name = f"atlas-{uid}-attachment.md"
        md_path = out_dir / md_name
        md_path.write_text(md_content, encoding="utf-8")

        # Allure result
        history_id = hashlib.sha1(f"review-code|{f}".encode()).hexdigest()
        labels = [
            {"name": "suite", "value": "review-code"},
            {"name": "severity", "value": max_severity},
            {"name": "atlas.score", "value": str(total)},
            {"name": "atlas.grade", "value": grade},
            {"name": "atlas.mode", "value": fr.get("mode", "static")},
            {"name": "atlas.ai_advisory", "value": "false"},
        ]
        result = {
            "uuid": uid,
            "historyId": history_id,
            "testCaseId": history_id,
            "name": f"review-code: {f}",
            "fullName": f"atlas.review-code.{f}",
            "status": status,
            "statusDetails": status_details,
            "stage": "finished",
            "start": 0,
            "stop": 0,
            "labels": labels,
            "parameters": [],
            "steps": steps,
            "attachments": [
                {"name": "review-report.md", "source": md_name,
                 "type": "text/markdown"}
            ],
            "links": [],
        }

        result_path = out_dir / f"atlas-{uid}-result.json"
        result_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        results_count += 1

    return {"results": results_count, "out": str(out_dir)}


def _details(message: str, trace: str = "") -> dict:
    """Allure statusDetails 统一形态（与 Gap5 同格式）。"""
    return {"known": False, "muted": False, "flaky": False,
            "message": message, "trace": trace}


# ── --to-tower 集成 ──────────────────────────────────────────────────────

def findings_to_tower(findings: list[dict], reports_dir: Path) -> int:
    """把 findings 写进审核塔（kind=code_review, pending_review）。

    返回写入的草稿数。每个 finding 一条草稿，去重靠 id。
    """
    from .experience import ExperienceJournal
    journal = ExperienceJournal(reports_dir)
    count = 0
    for f in findings:
        draft = journal.draft_from_text(
            task=f"code_review: {f['file']}:{f['line']}",
            text=f"{f['rule_id']} {f['rationale']}",
            failure_class="code_review",
            exception_type=f["rule_id"],
            kind="code_review",
            evidence={
                "rule_id": f["rule_id"],
                "severity": f["severity"],
                "dimension": f["dimension"],
                "category": f["category"],
                "file": f["file"],
                "line": f["line"],
                "snippet": f["snippet"],
                "rationale": f["rationale"],
                "suggestion": f["suggestion"],
            })
        journal.append_draft(draft)
        count += 1
    return count


# ── 主入口 ────────────────────────────────────────────────────────────────

def review_code(paths: list[str | Path], *, mode: str = "static",
                kind: str = "auto", format: str = "allure",
                out: str | Path | None = None,
                config_path: str | Path | None = None,
                baseline: str | Path | None = None,
                fail_under: int | None = None,
                to_tower: bool = False,
                reports_dir: str | Path | None = None,
                root: str | Path | None = None) -> dict:
    """主入口：扫描 → 评分 → 报告。

    返回 {"results": [per_file], "summary": {...}, "rc": int}。
    """
    config = load_config(config_path)
    targets = _iter_targets(paths, kind)
    if not targets:
        raise ValueError(f"未找到评审目标：{paths}")

    # knowledge 模式：加载选择器注册表
    registry = None
    if mode == "knowledge":
        try:
            from .plugin_api import resolve_plugin
            plugin = resolve_plugin()
            kb = plugin.knowledge()
            from .knowledge import SelectorRegistry
            # 组合层（私有 > 平台 > 通用）：影子层 `load` 会被平台自带的 components.json
            # 整份遮蔽，15 个通用组件对评审规则不可见 → SAFE-*/MAINT-* 的选择器核对
            # 会对「用了通用组件的合法用例」误报（实测影子 7 条 vs 组合 22 条）。
            components = kb.load_composed("components")
            registry = SelectorRegistry(components)
        except Exception:
            registry = None  # 知识库不可用时降级为 static

    file_results = []
    all_findings = []

    for file_path, file_kind in targets:
        file_str = str(file_path)
        findings = []

        if file_kind == "pytest":
            findings = scan_pytest_file(file_path, config, registry)
        elif file_kind == "dsl":
            try:
                flow = yaml.safe_load(file_path.read_text(encoding="utf-8",
                                                           errors="replace"))
                if isinstance(flow, dict):
                    findings = scan_dsl_flow(file_path, flow, config, registry)
                else:
                    if _rule_enabled("MAINT-006", config):
                        findings.append(_make_finding(
                            "MAINT-006", file_str, 1, "DSL 顶层非映射", config))
            except (OSError, yaml.YAMLError) as exc:
                if _rule_enabled("MAINT-006", config):
                    findings.append(_make_finding(
                        "MAINT-006", file_str, 1, str(exc), config))
        elif file_kind == "contract":
            findings = scan_contract_file(file_path, config)

        # 基线差分
        baseline_notes = []
        if baseline and file_kind == "pytest":
            baseline_path = Path(baseline)
            if baseline_path.is_file() and baseline_path.suffix == ".py":
                try:
                    # replace 回落（W3 追加项①）：UnicodeDecodeError 是 ValueError
                    # 不是 OSError，旧实现基线文件带坏字节时穿透 except 崩掉整份评审。
                    baseline_source = baseline_path.read_text(encoding="utf-8",
                                                              errors="replace")
                    current_source = file_path.read_text(encoding="utf-8",
                                                         errors="replace")
                    diff_findings = diff_baseline(
                        current_source, baseline_source, file_str, config)
                    findings.extend(diff_findings)
                    if diff_findings:
                        baseline_notes.append(
                            f"{file_str}: {len(diff_findings)} 条基线差分 finding")
                except OSError:
                    pass

        # 运行史交叉
        if baseline and file_kind == "pytest":
            runs_path = Path(baseline)
            if runs_path.is_file() and runs_path.suffix == ".jsonl":
                cross_findings = cross_check_runs(runs_path, findings, config)
                findings.extend(cross_findings)
                if cross_findings:
                    baseline_notes.append(
                        f"{file_str}: {len(cross_findings)} 条运行史交叉 finding")

        score = score_findings(findings, config)
        file_results.append({
            "file": file_str,
            "kind": file_kind,
            "findings": findings,
            "score": score,
            "mode": mode,
            "baseline_notes": baseline_notes,
        })
        all_findings.extend(findings)

    # 汇总
    total_score = sum(fr["score"]["total"] for fr in file_results)
    avg_score = round(total_score / len(file_results)) if file_results else 100
    # 取最低等级（最差）
    _GRADE_RANK = {"A": 0, "B": 1, "C": 2, "D": 3, "F": 4}
    # 起点必须是**最好**的等级：旧实现从这里 `max_grade = "F"` 起步，而 F 的 rank 已经是最大值
    # 4，`>` 比较永远不成立 → 聚合 grade **恒等于 F**（实测 `critical_findings=0`、`avg_score=99`
    # 的报告照样写 `grade: F`）。聚合等级是给人看的第一行结论，恒 F 等于把结论删掉。
    max_grade = "A"
    for fr in file_results:
        g = fr["score"]["grade"]
        if _GRADE_RANK.get(g, 4) > _GRADE_RANK.get(max_grade, 4):
            max_grade = g

    summary = {
        "files": len(file_results),
        "total_findings": len(all_findings),
        "critical_findings": sum(1 for f in all_findings if f["severity"] == "critical"),
        "avg_score": avg_score,
        "grade": max_grade,
        "mode": mode,
    }

    # 门禁判定
    rc = 0
    has_critical = any(f["severity"] == "critical" for f in all_findings)
    if has_critical or (fail_under is not None and avg_score < fail_under):
        rc = 1

    result = {
        "results": file_results,
        "summary": summary,
        "rc": rc,
    }

    # 输出
    if format == "json":
        return result
    elif format == "text":
        result["_text"] = format_text({**result, "findings": all_findings})
        return result
    elif format == "allure":
        out_dir = Path(out) if out else (
            Path(reports_dir) / "review-code-results" if reports_dir else
            Path("reports/review-code-results"))
        emit_allure_review(file_results, out_dir)
        result["_allure_out"] = str(out_dir)
        return result

    return result
