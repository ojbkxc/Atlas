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
import time
import uuid
from pathlib import Path
from typing import Any

import yaml

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
_CREDENTIAL_PATTERNS = [
    re.compile(r"password\s*=\s*['\"][^'\"]{3,}['\"]", re.IGNORECASE),
    re.compile(r"passwd\s*=\s*['\"][^'\"]{3,}['\"]", re.IGNORECASE),
    re.compile(r"token\s*=\s*['\"][^'\"]{8,}['\"]", re.IGNORECASE),
    re.compile(r"api_key\s*=\s*['\"][^'\"]{8,}['\"]", re.IGNORECASE),
    re.compile(r"secret\s*=\s*['\"][^'\"]{8,}['\"]", re.IGNORECASE),
    re.compile(r"https?://[^\s'\"]+", re.IGNORECASE),
]

# 选择器模式（MAINT-002）：CSS/XPath 选择器字面量
_SELECTOR_PATTERN = re.compile(
    r"['\"](\.app-main|\.el-table|\.el-button|#\w+|\[.*?\]|//\w+|/html)['\"]")

# 写死数据模式（MAINT-003）：历史单号/客户号
_HARDCODED_DATA_PATTERN = re.compile(
    r"['\"]((?:SO|SO-|CUST|PT|DEMO)[-_]?\d{4,})['\"]", re.IGNORECASE)

# 注释掉的断言（ASSERT-006）
_COMMENTED_ASSERT = re.compile(r"#\s*assert\b")

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
    deductions = config.get("severity_deductions", SEVERITY_DEDUCTIONS)
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

    def __init__(self, file: str, source_lines: list[str], config: dict):
        self.file = file
        self.source_lines = source_lines
        self.config = config
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
        # assert isinstance(x, T)
        if isinstance(test, ast.Call):
            func = test.func
            if isinstance(func, ast.Name) and func.id == "isinstance":
                self._add("ASSERT-002", node.lineno, snippet)
                return
            # assert len(x) > 0
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
        # assert abs(a-b) < 0.01
        if isinstance(test, ast.Compare) and isinstance(test.left, ast.Call):
            left_func = test.left.func
            if isinstance(left_func, ast.Name) and left_func.id == "abs":
                self._add("ASSERT-007", node.lineno, snippet)

    def visit_ExceptHandler(self, node):
        """GREEN-001: except: pass / except Exception: pass。"""
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
                    self._add("GREEN-001", node.lineno, snippet)
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
            # time.sleep
            if attr == "sleep" and isinstance(func.value, ast.Name) and func.value.id == "time":
                self._add("MAINT-001", node.lineno, snippet)
            # pytest.skip / pytest.xfail
            if attr == "skip" and isinstance(func.value, ast.Name) and func.value.id == "pytest":
                self._add("GREEN-003", node.lineno, snippet)
        elif isinstance(func, ast.Name):
            if func.id == "sleep":
                # bare sleep(...) call
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


def _scan_pytest_line_level(file: str, source: str, config: dict,
                            findings: list[dict]) -> None:
    """行级扫描：注释掉的断言、type: ignore/noqa、硬编码凭据/选择器/数据。"""
    lines = source.splitlines()
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        # ASSERT-006: 注释掉的断言
        if _COMMENTED_ASSERT.search(line):
            if _rule_enabled("ASSERT-006", config):
                findings.append(_make_finding("ASSERT-006", file, i, stripped, config))
        # GREEN-004: type: ignore / noqa
        if _MASK_PATTERN.search(line):
            if _rule_enabled("GREEN-004", config):
                findings.append(_make_finding("GREEN-004", file, i, stripped, config))
        # SAFE-001: 硬编码凭据/URL
        for pattern in _CREDENTIAL_PATTERNS:
            if pattern.search(line):
                if _rule_enabled("SAFE-001", config):
                    findings.append(_make_finding("SAFE-001", file, i, stripped, config))
                break
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
    source = path.read_text(encoding="utf-8")
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

    scanner = _PytestAstScanner(file_str, source.splitlines(), config)
    scanner.visit(tree)
    findings.extend(scanner.findings)

    # 行级扫描
    _scan_pytest_line_level(file_str, source, config, findings)

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
    except Exception:
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


def _iter_targets(paths: list[str | Path], kind: str) -> list[tuple[Path, str]]:
    """展开路径列表为 (file, kind) 列表。"""
    results = []
    for p in paths:
        path = Path(p)
        if path.is_dir():
            for child in sorted(path.rglob("*")):
                if child.is_file():
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
                except Exception:
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

    try:
        raw = runs_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raw = runs_path.read_text(encoding="utf-8", errors="replace")

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
        identity = f"{f['file']}|{f['line']}|{f['rule_id']}"
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
            components = kb.load("components")
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
                flow = yaml.safe_load(file_path.read_text(encoding="utf-8"))
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
                    baseline_source = baseline_path.read_text(encoding="utf-8")
                    current_source = file_path.read_text(encoding="utf-8")
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
    max_grade = "F"
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
