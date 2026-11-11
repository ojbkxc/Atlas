"""C5 边界账本判据核（design_c5 §2/§3，评审 C5-A 上移裁决）。

红线 #11 的落点：**账本是数据，不是引擎字面量**——本模块只实现「怎么读、怎么判」
的纯通用逻辑（校验/特异度裁决/diff 分类），`docs/ownership/atlas-boundaries.json`
里才是路径与决策数据。引擎运行时（dsl/engine/actor…）永不 import 本模块；消费方
只有两个薄入口：`atlas doctor`（一致性项）与 `scripts/boundary_classify.py`
（merge/吸收前的入站变更集分类 gate）。

判据唯一实现在这里（#92）：doctor 与分类脚本都调同一组函数，不留第二份校验逻辑。

跨平台条款（design_c5 §5）：X1 路径全 POSIX 相对串、glob 匹配走分段 `fnmatchcase`
语义（`*` 不跨段、`**` 跨段），不触文件系统差异；X2 零 subprocess；X5 读侧坏字节
replace 回落（#112/#131 同纪律）；账本读写 UTF-8 显式。
"""
from __future__ import annotations

import json
from datetime import datetime
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath

SCHEMA_VERSION = 1
LEDGER_REL_PATH = "docs/ownership/atlas-boundaries.json"

# 三值枚举（gap6 §3.1-C5：region 是枚举；risk 不是——风险等级由 structural 行数
# 阈值从变更集推得，账本条目里不写 risk 字段，评审上游注记钉死这条口径）。
REGION_VALUES = ("engine-owned", "absorbed", "private-knowledge")
# disposition："" | reduce | accepted（C17 同族，技术债显式标价）。
DISPOSITION_VALUES = ("", "reduce", "accepted")
LOG_FIELDS = ("date", "decision", "rationale", "by")

# 跨文件搬移启发式阈值（design_c5 §3）：归一化行集 Jaccard ≥ 0.8 判 structural。
# 误报代价 = 多一次人审（§8 明记可接受）；漏判由 C3 哨兵兜底，二者互为交叉验证。
MOVE_JACCARD = 0.8

_BS = chr(92)  # 不在源码字面量里写反斜杠（X1 哨兵对反斜杠形态敏感）
_TOWER_REGIONS = ("absorbed", "engine-owned")  # structural 命中这两区才进人审塔


def ledger_path(root: Path) -> Path:
    """账本位置由仓库根推出；不猜、不搜盘。"""
    return Path(root) / PurePosixPath(LEDGER_REL_PATH)


# ── 读侧容错（#112/#131 同纪律：坏字节 replace、坏形态点名，绝不裸崩） ──────────

def load_ledger(path: Path) -> tuple[dict, list[str]]:
    """(账本, 问题清单)。文件缺失/坏字节/坏 JSON/形态不对一律点名返回，不抛。

    doctor 用它区分 present:false（文件不在，如实报缺失、不改 rc）与
    present-but-broken（漂移，rc1）；分类 gate 拿 problems 非空就 rc2——
    没有可信账本就没有分类，宁可拒绝也不猜。
    """
    path = Path(path)
    if not path.is_file():
        return {}, [f"账本文件缺失: {LEDGER_REL_PATH}（期望 {path}）"]
    try:
        raw = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raw = path.read_text(encoding="utf-8", errors="replace")  # 腰斩尾行：坏字节进 JSON 必败，点名不崩
    except OSError as exc:
        return {}, [f"账本不可读: {exc}"]
    try:
        data = json.loads(raw)
    except ValueError as exc:
        return {}, [f"账本 JSON 解析失败: {exc}"]
    if not isinstance(data, dict):
        return {}, [f"账本顶层必须是映射，实得 {type(data).__name__}"]
    # 有问题的账本也原样带回：doctor 要在 present-but-broken 时如实展示 regions 计数
    # 与残缺字段；分类 gate 拿 problems 非空即 rc2，不会误用残破账本。
    return data, validate_ledger(data)


# ── 字段级校验（账本合法性的唯一判据） ─────────────────────────────────────────

def validate_ledger(data: dict) -> list[str]:
    """逐条点名问题；空列表=账本合法。纯函数，不触文件系统。"""
    problems: list[str] = []
    ver = data.get("schema_version")
    if isinstance(ver, bool) or not isinstance(ver, int):
        problems.append(f"schema_version 必须是整数，实得 {type(ver).__name__}")
    elif ver != SCHEMA_VERSION:
        problems.append(f"schema_version={ver} 本版判据不支持（实现认 {SCHEMA_VERSION}），先升级判据再改账本")

    rm = data.get("risk_model")
    if not isinstance(rm, dict):
        problems.append("risk_model 缺失或非映射")
    else:
        if rm.get("basis") != "structural_drift":
            problems.append(f"risk_model.basis 必须是 'structural_drift'（分类轴 cosmetic/structural），实得 {rm.get('basis')!r}")
        th = rm.get("thresholds")
        if not isinstance(th, dict):
            problems.append("risk_model.thresholds 缺失或非映射")
        else:
            med, high = th.get("structural_medium"), th.get("structural_high")
            for key, val in (("structural_medium", med), ("structural_high", high)):
                if isinstance(val, bool) or not isinstance(val, int) or val < 1:
                    problems.append(f"risk_model.thresholds.{key} 必须是正整数，实得 {val!r}")
            if isinstance(med, int) and isinstance(high, int) and not med < high:
                problems.append(f"thresholds 需 medium({med}) < high({high})")

    regions = data.get("regions")
    if not isinstance(regions, list) or not regions:
        problems.append("regions 必须是非空列表（空账本=什么都没归类，一致性检查失去对象）")
        return problems

    seen_ids: dict[str, int] = {}
    entries: list[tuple[int, str, list[str]]] = []  # (index, region_id, paths) 供重叠裁决
    for i, reg in enumerate(regions):
        label = f"regions[{i}]"
        if not isinstance(reg, dict):
            problems.append(f"{label} 必须是映射，实得 {type(reg).__name__}")
            continue
        rid = reg.get("id")
        if not isinstance(rid, str) or not rid.strip():
            problems.append(f"{label} 缺少非空 id")
        else:
            label = f'region "{rid}"'
            if rid in seen_ids:
                problems.append(f'{label} id 与 regions[{seen_ids[rid]}] 重复')
            else:
                seen_ids[rid] = i
        if reg.get("region") not in REGION_VALUES:
            problems.append(f"{label} region {reg.get('region')!r} 不在三值枚举 {REGION_VALUES}")
        if reg.get("disposition", "") not in DISPOSITION_VALUES:
            problems.append(f"{label} disposition {reg.get('disposition')!r} 不在枚举 {DISPOSITION_VALUES}")
        for flag in ("style_paths", "optional"):
            if flag in reg and not isinstance(reg[flag], bool):
                problems.append(f"{label} {flag} 必须是布尔，实得 {type(reg[flag]).__name__}")
        paths = reg.get("paths")
        if not isinstance(paths, list) or not paths:
            problems.append(f"{label} paths 必须是非空列表")
            paths = []
        for p in paths:
            if not isinstance(p, str) or not p:
                problems.append(f"{label} paths 含非字符串/空项: {p!r}")
                continue
            # X1：POSIX 相对串——反斜杠、绝对路径、盘符、.. 逃逸都点名
            if _BS in p:
                problems.append(f'{label} 路径含反斜杠（X1 要求 POSIX 相对串）: "{p}"')
            if p.startswith("/") or (len(p) >= 2 and p[1] == ":" and p[0].isalpha()):
                problems.append(f'{label} 路径必须是仓库相对（拒绝对外形态）: "{p}"')
            if ".." in PurePosixPath(p.replace(_BS, "/")).parts:
                problems.append(f'{label} 路径含 ".." 逃逸: "{p}"')
        entries.append((i, rid if isinstance(rid, str) else label, list(x for x in paths if isinstance(x, str))))

        log = reg.get("decision_log", [])
        if not isinstance(log, list):
            problems.append(f"{label} decision_log 必须是列表")
            log = []
        for j, d in enumerate(log):
            if not isinstance(d, dict):
                problems.append(f"{label} decision_log[{j}] 必须是映射")
                continue
            for field in LOG_FIELDS:
                v = d.get(field)
                if not isinstance(v, str) or not v.strip():
                    problems.append(f"{label} decision_log[{j}] 缺 {field}（每条必带 date/decision/rationale/by）")
            ds = d.get("date")
            if isinstance(ds, str) and ds.strip():
                try:
                    datetime.strptime(ds, "%Y-%m-%d")
                except ValueError:
                    problems.append(f"{label} decision_log[{j}] date 需 YYYY-MM-DD，实得 {ds!r}")
        # C17 债标价：说不清怎么消除的债不算 accepted——必须至少一条带理由的决策
        if reg.get("disposition") == "accepted" and not log:
            problems.append(f"{label} disposition=accepted 但 decision_log 为空：技术债必须显式标价（何时、为何付这个代价）")

    # 重叠裁决：最长非通配前缀/更特异 glob 优先；同特异度认领同一路径 → 账本非法
    for x in range(len(entries)):
        for y in range(x + 1, len(entries)):
            ia, rida, pa = entries[x]
            ib, ridb, pb = entries[y]
            rida, ridb = _region_value(data, ia), _region_value(data, ib)
            for a in pa:
                for b in pb:
                    if not _may_overlap(a, b):
                        continue
                    if _specificity(a) == _specificity(b) and _differs_in_region(data, ia, ib):
                        problems.append(
                            f'重叠冲突：region "{rida}" 的 "{a}" 与 region "{ridb}" 的 "{b}" '
                            f"可认领同一路径且特异度相同——裁决规则（最长非通配前缀优先）失效，改账本")
    return problems


def _region_value(data: dict, index: int) -> object:
    regions = data.get("regions")
    if isinstance(regions, list) and 0 <= index < len(regions) and isinstance(regions[index], dict):
        return regions[index].get("id")
    return None


def _differs_in_region(data: dict, ia: int, ib: int) -> bool:
    """同区域内两条规则认领同一路径无害（裁决结果同一个 region）；跨区才要裁决。"""
    return _region_value(data, ia) != _region_value(data, ib)


# ── glob 语义（X1：分段 fnmatch，`*` 不跨段、`**` 跨段；尾斜杠=子树） ────────────

def _split(pattern: str) -> tuple[list[str], bool]:
    subtree = pattern.endswith("/")
    segs = [s for s in pattern.split("/") if s and s != "."]
    return segs, subtree


def _rank(seg: str) -> int:
    if seg == "**":
        return 3
    if "*" in seg:
        return 2
    if "?" in seg:
        return 1
    return 0


def _norm(pattern: str) -> list[str]:
    segs, subtree = _split(pattern)
    return segs + (["**"] if subtree else [])


def _literal_prefix_len(segs: list[str]) -> int:
    n = 0
    for s in segs:
        if "*" in s or "?" in s:
            break
        n += 1
    return n


def _specificity(pattern: str) -> tuple:
    """特异度排序键——**越小越特异**（resolve_region 取 min，validator 比相等）。

    最长非通配前缀优先（lp 取负）；同前缀比逐段 rank（字面 < ? < * < **）；
    仍同则段多者胜（len 取负）。design_c5 §2 裁决规则的机器形态。
    """
    segs = _norm(pattern)
    return (-_literal_prefix_len(segs), tuple(_rank(s) for s in segs), -len(segs))


def _match_segs(p: list[str], i: int, r: list[str], j: int) -> bool:
    if i == len(p):
        return j == len(r)
    if p[i] == "**":
        # ** 匹配零个或多个整段
        return any(_match_segs(p, i + 1, r, k) for k in range(j, len(r) + 1))
    if j == len(r):
        return False
    return fnmatchcase(r[j], p[i]) and _match_segs(p, i + 1, r, j + 1)


def path_matches(pattern: str, rel_path: str) -> bool:
    """POSIX 相对路径能否被 region 认领（纯字符串语义，两台机器同一答案）。"""
    rel = rel_path.replace(_BS, "/")
    r = [s for s in rel.split("/") if s and s != "."]
    return _match_segs(_norm(pattern), 0, r, 0)


def _segs_range(pattern: str) -> tuple[int, int]:
    segs = _norm(pattern)
    return len(segs), (10 ** 9 if "**" in segs else len(segs))


def _segs_compat(a: str, b: str) -> bool:
    """两段 pattern 是否存在公共匹配串（保守判：wild×wild 一律兼容）。"""
    a_lit, b_lit = _rank(a) == 0, _rank(b) == 0
    if a_lit and b_lit:
        return a == b
    if a_lit:
        return fnmatchcase(a, b)
    if b_lit:
        return fnmatchcase(b, a)
    return True


def _may_overlap(p: str, q: str) -> bool:
    """两个 pattern 是否可能认领同一路径——证不出不相交就算重叠（保守）。"""
    ps, qs = _norm(p), _norm(q)
    pmin, pmax = _segs_range(p)
    qmin, qmax = _segs_range(q)
    if pmax < qmin or qmax < pmin:
        return False
    return _overlap_walk(ps, 0, qs, 0)


def _overlap_walk(ps: list[str], i: int, qs: list[str], j: int) -> bool:
    if i == len(ps) and j == len(qs):
        return True
    if i == len(ps):
        return all(s == "**" for s in qs[j:])
    if j == len(qs):
        return all(s == "**" for s in ps[i:])
    if ps[i] == "**":
        return _overlap_walk(ps, i + 1, qs, j) or _overlap_walk(ps, i, qs, j + 1)
    if qs[j] == "**":
        return _overlap_walk(ps, i, qs, j + 1) or _overlap_walk(ps, i + 1, qs, j)
    return _segs_compat(ps[i], qs[j]) and _overlap_walk(ps, i + 1, qs, j + 1)


# ── 区域裁决（最长特异度优先） ─────────────────────────────────────────────────

def region_by_id(ledger: dict, region_id: str | None) -> dict | None:
    if not region_id:
        return None
    for reg in ledger.get("regions") or []:
        if isinstance(reg, dict) and reg.get("id") == region_id:
            return reg
    return None


def resolve_region(ledger: dict, rel_path: str) -> str | None:
    """返回最特异 region 的 id；None=未登记（gate 提示补账，不猜归类）。

    合法账本不存在跨区同特异度歧义（validate_ledger 已判非法）；账本非法时
    本函数仍按「先登记先赢」确定性地返回——调用方（classify gate）拿到 problems
    就该 rc2 拒绝分类，不该走到这里。
    """
    best: tuple | None = None
    best_id: str | None = None
    for reg in ledger.get("regions") or []:
        if not isinstance(reg, dict):
            continue
        paths = reg.get("paths")
        if not isinstance(paths, list):
            continue
        for p in paths:
            if isinstance(p, str) and path_matches(p, rel_path):
                spec = _specificity(p)
                if best is None or spec < best:  # 排序键越小越特异（最长非通配前缀/更具体 glob 优先）
                    best, best_id = spec, reg.get("id")
    return best_id if isinstance(best_id, str) else None


def decision_log_tail(reg: dict | None) -> dict | None:
    """decisionLog 尾部摘要——重议防御的可见性（design_c5 §6.5）。"""
    if not isinstance(reg, dict):
        return None
    log = reg.get("decision_log")
    if isinstance(log, list) and log and isinstance(log[-1], dict):
        return dict(log[-1])
    return None


def accepted_debt_summary(ledger: dict) -> list[dict]:
    """doctor 展示项：所有 accepted 债 + 尾部决策（哪些代价是被明码接受过的）。"""
    out = []
    for reg in ledger.get("regions") or []:
        if isinstance(reg, dict) and reg.get("disposition") == "accepted":
            out.append({"id": reg.get("id"), "decision_log_tail": decision_log_tail(reg)})
    return out


# ── 磁盘覆盖（doctor 的 boundary_assets_consistent；X9 读写之外只做只读探测） ────

def ledger_disk_coverage(root: Path, ledger: dict) -> tuple[list[str], list[str]]:
    """(warnings, 未命中的必选 region)。optional region 全路径缺失只 warn（评审 C5-B）。"""
    warnings: list[str] = []
    uncovered: list[str] = []
    root = Path(root)
    for reg in ledger.get("regions") or []:
        if not isinstance(reg, dict):
            continue
        paths = [p for p in (reg.get("paths") or []) if isinstance(p, str)]
        if any(_pattern_hits(root, p) for p in paths):
            continue
        rid = reg.get("id")
        if reg.get("optional") is True:
            warnings.append(f'region "{rid}" 全部路径不在磁盘（optional：私有知识/迁移产物不入库，属正常）')
        else:
            uncovered.append(str(rid))
    return warnings, uncovered


def _pattern_hits(root: Path, pattern: str) -> bool:
    """账本路径至少命中一条磁盘条目。字面路径直接探测；含通配按字面前缀目录扫。"""
    segs = _norm(pattern)
    lit = []
    for s in segs:
        if s == "**" or "*" in s or "?" in s:
            break
        lit.append(s)
    if len(lit) == len(segs):  # 无通配：文件或目录存在即命中
        return root.joinpath(*segs).exists() if segs else True
    if not lit:
        base = root
    else:
        base = root.joinpath(*lit)
        if not base.is_dir():
            return False
    for cand in base.rglob("*"):
        if cand.is_file():
            rel = cand.relative_to(root).as_posix()
            if path_matches(pattern, rel):
                return True
    # 子树/目录型 pattern 允许目录本身算命中（如 flows/iservice/ 存在但暂空）
    if pattern.endswith("/") and base.is_dir():
        return True
    return False


# ── 入站变更集分类（merge/吸收 gate 的语义核） ──────────────────────────────────

def _unquote_git_path(raw: str) -> str:
    """git 默认 core.quotepath=true 会把非 ASCII 路径转成 "a/\346\226\207" 八进制串；
    分类侧还原成 POSIX 相对路径，还原失败就保留原文点名（不猜）。"""
    raw = raw.strip()
    if not (len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"'):
        return raw
    body = raw[1:-1]
    out = bytearray()
    i = 0
    while i < len(body):
        ch = body[i]
        if ch == "\\" and i + 1 < len(body):
            nxt = body[i + 1]
            if nxt in "01234567" and i + 4 <= len(body):
                try:
                    out.append(int(body[i + 1:i + 4], 8))
                    i += 4
                    continue
                except ValueError:
                    pass
            out.append(ord(nxt))
            i += 2
            continue
        out.append(ord(ch))
        i += 1
    return out.decode("utf-8", errors="replace")


def _strip_ab(prefix: str, raw: str) -> str:
    """去掉 diff 头的 a/ b/ 前缀；/dev/null 由调用方处理。"""
    p = _unquote_git_path(raw)
    if p.startswith(prefix + "/"):
        return p[len(prefix) + 1:]
    return p


def parse_unified_diff(text: str) -> dict[str, dict]:
    """git diff 产物 → {file: {added, removed, moved_files}}。自写解析（零依赖）。

    只认 unified diff 的确定形态（design_c5 §3「不给 classify 造 git 调用面」——
    diff 从 stdin/文件来，git 命令由人执行，X2）。rename/copy 行把两端标成互认的
    搬移对；hunk 正文行逐条归类。识别不了的行跳过，不硬猜。
    """
    files: dict[str, dict] = {}
    current: str | None = None
    old_path: str | None = None

    def entry(name: str) -> dict:
        return files.setdefault(name, {"added": [], "removed": [], "moved_files": set()})

    for line in text.splitlines():
        if line.startswith("diff --git "):
            tail = line[len("diff --git "):]
            # "a/x b/y"：从右侧找分隔最稳（路径含空格时 b/ 侧仍准确）
            if " b/" in tail:
                current = tail.rsplit(" b/", 1)[1]
                entry(_unquote_git_path(current))
            continue
        if line.startswith("rename from "):
            old_path = _strip_ab("a", line[len("rename from "):])
            entry(old_path)["moved_files"].add("__pending__")
            continue
        if line.startswith("rename to "):
            new_path = _strip_ab("b", line[len("rename to "):])
            e_new, e_old = entry(new_path), entry(old_path or new_path)
            e_new["moved_files"].add(old_path or new_path)
            e_old["moved_files"].discard("__pending__")
            e_old["moved_files"].add(new_path)
            current = new_path
            continue
        if line.startswith("copy from "):
            old_path = _strip_ab("a", line[len("copy from "):])
            entry(old_path)
            continue
        if line.startswith("copy to "):
            new_path = _strip_ab("b", line[len("copy to "):])
            e_new, e_old = entry(new_path), entry(old_path or new_path)
            e_new["moved_files"].add(old_path or new_path)
            if old_path:
                e_old["moved_files"].add(new_path)
            current = new_path
            continue
        if line.startswith("--- "):
            cand = line[4:].strip()
            old_path = None if cand == "/dev/null" else _strip_ab("a", cand)
            continue
        if line.startswith("+++ "):
            cand = line[4:].strip()
            if cand == "/dev/null":
                current = old_path
            else:
                current = _strip_ab("b", cand)
            if current:
                entry(current)
            continue
        if line.startswith(("@@", "index ", "old mode", "new mode", "similarity index",
                             "dissimilarity index", "Binary files", "\\")):
            continue
        if current is not None:
            if line.startswith("+"):
                entry(current)["added"].append(line[1:])
            elif line.startswith("-"):
                entry(current)["removed"].append(line[1:])
    return files


def _normalized(lines: list[str]) -> set[str]:
    return {s.strip() for s in lines if s.strip()}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def classify_change(ledger: dict, files: dict) -> list[dict]:
    """变更集逐文件分级：class(cosmetic|structural) + risk(low|medium|high)。

    判序（design_c5 §3）：style_paths 区恒 cosmetic → 跨文件搬移（归一化行集
    Jaccard≥0.8）与 rename/copy 恒 structural → 行数阈值（medium/high 来自
    **账本**，不写死在代码里——#11）。verdict 标记 ASCII（X4）：
    REVIEW-TOWER=absorbed/engine-owned 命中 structural 进人审塔；AUTO=cosmetic
    放行；HINT=路径未登记，先补账；DATA-REGION=private-knowledge 数据区（业务
    内容漂移不是引擎边界问题，只提示不进塔）。
    """
    th = (ledger.get("risk_model") or {}).get("thresholds") or {}
    med, high = th.get("structural_medium"), th.get("structural_high")
    if not isinstance(med, int) or not isinstance(high, int):
        raise ValueError("账本 risk_model.thresholds 缺失/非整数——先过 validate_ledger 再分类")

    added_sets = {f: _normalized(e["added"]) for f, e in files.items()}
    removed_sets = {f: _normalized(e["removed"]) for f, e in files.items()}
    moved: set[str] = set()
    names = list(files)
    for a in names:  # A 删除的行在 B 新增里成批出现 = 跨文件搬移
        ra = removed_sets[a]
        for b in names:
            if b == a:
                continue
            if _jaccard(ra, added_sets[b]) >= MOVE_JACCARD:
                moved.update((a, b))
    for f, e in files.items():
        for partner in e.get("moved_files") or ():
            if partner in files:
                moved.update((f, partner))

    items: list[dict] = []
    for f in names:
        e = files[f]
        churn = len(e["added"]) + len(e["removed"])
        rid = resolve_region(ledger, f)
        reg = region_by_id(ledger, rid)
        reason = "churn_threshold"
        if reg and reg.get("style_paths") is True:
            cls, risk, reason = "cosmetic", "low", "style_paths"
        elif f in moved:
            cls = "structural"
            risk = "high" if churn >= high else "medium"
            reason = "cross_file_move"
        else:
            cls = "cosmetic" if churn < med else "structural"
            risk = "low" if cls == "cosmetic" else ("high" if churn >= high else "medium")
        if rid is None:
            verdict = "HINT"
        elif cls == "cosmetic":
            verdict = "AUTO"
        elif reg.get("region") in _TOWER_REGIONS:
            verdict = "REVIEW-TOWER"
        else:
            verdict = "DATA-REGION"
        items.append({"file": f, "region": rid, "class": cls, "risk": risk,
                      "churn": churn, "reason": reason,
                      "disposition_hint": (reg or {}).get("disposition", ""),
                      "decision_log_tail": decision_log_tail(reg),
                      "verdict": verdict})
    return items


# ── 进塔草稿形态（复用既有塔与 _atomic_write，不新开塔——红线 #9/#10） ──────────

def boundary_draft_args(item: dict, diff_origin: str) -> dict:
    """REVIEW-TOWER 项 → ExperienceJournal.draft_from_text 的关键字参数。

    command/exit_code/assertions/verdict 三元组位**留空**（design_c5 §3）：这类
    草稿天然 unverified，晋级照走 C2 闸——「不自动写知识」的语义由既有闸承接，
    分类器不另造证据也不伪造证据。
    """
    tail = item.get("decision_log_tail") or {}
    text = (f"边界漂移 {item['class']}/{item['risk']}：{item['file']}"
            f"（churn={item['churn']}，判据={item['reason']}，来源={diff_origin}）")
    if tail:
        text += f"；最近决策 {tail.get('date')} {tail.get('decision')}: {str(tail.get('rationale'))[:80]}"
    return {
        "task": f"boundary:{item.get('region')}:{item['file']}",
        "text": text,
        "failure_class": "boundary_drift",
        "exception_type": item["class"],
        "pitfall_id": str(item.get("region") or ""),
        "kind": "boundary_review",
        "evidence": {
            "file": item["file"],
            "region": item.get("region"),
            "class": item["class"],
            "risk": item["risk"],
            "churn": item["churn"],
            "reason": item["reason"],
            "disposition": item.get("disposition_hint", ""),
            "decision_log_tail": item.get("decision_log_tail"),
            "diff_origin": diff_origin,
            "ledger": LEDGER_REL_PATH,
        },
    }
