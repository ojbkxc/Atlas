"""C3 迁移验收三件套 · 失败集合子集对比闸门（design_c3 §4；W3 owner: impl_migrate）。

判据：**整合后 pytest 失败集合 ⊆ 迁移前基线失败集合**。新增失败=迁移引入的破损（红，
逐条点名）；减少=改善（列出）。禁跳测试/降标准制造绿灯——本脚本不存在任何 skip/白名单
通道（§9「不做」项），造假绿在这里是结构性不可能，不是靠自觉。

基线事实（gap6 §9 E4 勘误已钉）：磁盘上没有 `case-migration-mapping.csv`，只有
gitignore 的 `docs/case-migration-mapping.md`——那是 TC→flow **映射清单**，不是失败
集合基线；本 gate 的基线是它之外新建的产物 `reports/migration/failures-baseline.json`
（reports/ 已 gitignore：测试名含内部用例号，敏感口径与映射表同级）。

「收集 0 项却 rc0」巡检点：`-k` 打错/路径过滤会让 pytest 空跑还退出 0（fork-sync :80
案例同款静默）——collected==0 一律 GATE-SKIP-EMPTY + rc1 点名，绝不绿。

rc 口径：0 子集成立 / 1 新增失败、静默空跑或 pytest 自身崩溃、超时 / 2 用户错误
（无基线、基线 JSON 坏——坏 JSON 点名「先重录基线」，不静默空比）。

跨平台（§7）：X1 scope 全 pathlib；X2 pytest 调用固定 argv list，`-k` 值永不进 shell、
无 shell=True；X4 输出 ASCII 标记 GATE-OK/GATE-RED/GATE-SKIP-EMPTY（中文正文 GBK 安全）；
X5 基线写盘原子 replace + 尾断行，读侧坏 JSON 点名；X7 不发信号，超时只靠
subprocess.run(timeout)（子 pytest 继承 GBK 控制台环境——env 统一
PYTHONIOENCODING=utf-8 + text/encoding utf-8 + errors=replace，memory PT 坑直修）。

纯离线：本脚本真跑的是仓库 pytest（本地、无网络），不触真机；子进程 argv 形态由
tests/test_migration_gate.py 钉桩（X2）。
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_TIMEOUT_S = 3600  # 全量离线套件上限；挂死按失败处理（feedback：无日志≠在跑）
BASELINE_REL = Path("reports") / "migration" / "failures-baseline.json"

_FAILED_RE = re.compile(r"^(?:FAILED|ERROR) (\S+)", re.M)
_COLLECTED_RE = re.compile(r"collected (\d+) items?")
# `-q --tb=no -rf`（磁盘核定 pytest 9）不打印 collected 头行，只打尾计数行
# （"1 failed, 1 passed in 0.02s" / "2 deselected" / "1 error"）。已执行数按
# 计数行 token 求和；deselected **不计**——`-k` 打错的空跑必须算 0（巡检点）。
_EXEC_RE = re.compile(r"(\d+) (?:passed|failed|errors?|skipped|xfailed|xpassed|interrupted)\b")


def run_pytest(root: Path, extra_args: list[str] | None = None,
               timeout: int = DEFAULT_TIMEOUT_S) -> tuple[set[str], int | None, int]:
    """跑离线 pytest，返回 (失败 node id 集合, collected|None, pytest rc)。

    argv 固定 [sys.executable, "-m", "pytest", "-q", "--tb=no", "-rf",
    "--disable-warnings", *extra]（X2/X5/X7 全套）；encoding utf-8 + replace。
    collected 无匹配（pytest 内部错误没打印计数）返回 None——调用侧按崩溃点名。
    """
    env = dict(os.environ)
    src = str(root / "src")
    env["PYTHONPATH"] = src + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["PYTHONIOENCODING"] = "utf-8"
    argv = [sys.executable, "-m", "pytest", "-q", "--tb=no", "-rf",
            "--disable-warnings", *(extra_args or [])]
    proc = subprocess.run(argv, cwd=root, text=True, encoding="utf-8",
                          errors="replace", env=env, timeout=timeout,
                          capture_output=True)
    failures, collected = parse_pytest_output(proc.stdout + proc.stderr)
    return failures, collected, proc.returncode


def parse_pytest_output(text: str) -> tuple[set[str], int | None]:
    """解析短格式输出：行首 `FAILED `/`ERROR ` 收 node id；collected 收已执行总数。

    collected 三形态（§8.3 样本 + 磁盘核定）：
    ① `-v`/样本头行 `collected N items` 优先直取；
    ② 无头行时（pytest 9 的 `-q` 实测**不打** collected 头）按尾计数行求和
       `1 failed, 1 passed in…`——**deselected 不计**：`-k` 打错表现为
       「2 deselected」，计入会把静默空跑读成跑了 2 条（巡检点反而失效）；
    ③ 两者都无 → None（pytest 内部崩没打印计数）——调用侧按崩溃点名，
       绝不当 0 静默通过。
    CRLF 归一；`FAILED x::y - AssertionError` 按首个空白切 token。
    """
    text = text.replace("\r\n", "\n")
    failures = set(_FAILED_RE.findall(text))
    head = _COLLECTED_RE.findall(text)
    if head:
        return failures, max(int(n) for n in head)
    counts = [int(n) for n in _EXEC_RE.findall(text)]
    if counts:
        return failures, sum(counts)
    return failures, None


def compare(baseline: set[str], current: set[str]) -> dict:
    """三态：subset_ok（current ⊆ baseline）/ new_failures（新增，阻断）/ resolved（改善）。"""
    return {
        "subset_ok": current <= baseline,
        "new_failures": sorted(current - baseline),
        "resolved": sorted(baseline - current),
    }


def baseline_path(root: Path, override: str | None) -> Path:
    return Path(override) if override else root / BASELINE_REL


def write_baseline(path: Path, failures: set[str], collected: int, root: Path) -> None:
    """X5：原子写（tmp→replace）+ 尾断行；目录不存在自建。"""
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "count": len(failures),
        "collected": collected,
        "root": str(root.resolve()),
        "failures": sorted(failures),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)


def load_baseline(path: Path) -> list[str] | None:
    """返回基线失败清单；文件不存在 None（rc2 指引）；坏 JSON/形态不对抛 ValueError
    （调用侧点名「先重录基线」——不静默空比，静默空比=任何 current 都「⊆ ∅ 之外全放」）。
    """
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ValueError(f"基线 JSON 无法解析：{path}（{exc}）——先重录基线") from exc
    if not isinstance(data, dict) or not isinstance(data.get("failures"), list):
        raise ValueError(f"基线形态不对（缺 failures 清单）：{path}——先重录基线")
    bad = [x for x in data["failures"] if not isinstance(x, str)]
    if bad:
        raise ValueError(f"基线含非字符串条目 {len(bad)} 条：{path}——先重录基线")
    return list(data["failures"])


def main(argv: list[str] | None = None, *, runner=run_pytest) -> int:
    ap = argparse.ArgumentParser(prog="migration_gate",
                                 description="迁移失败集合子集闸门（design_c3 §4）")
    ap.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    ap.add_argument("--record-baseline", action="store_true",
                    help="把当前失败集合快照为基线（reports/migration/failures-baseline.json）")
    ap.add_argument("--baseline", default=None, help="基线文件覆盖路径（默认 root 下）")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S)
    ap.add_argument("--pytest-arg", action="append", default=[],
                    help="透传给 pytest 的额外 argv（可重复；值永不进 shell）")
    args = ap.parse_args(argv)
    root = Path(args.root)

    if not root.is_dir():
        print(f"GATE-RED 错误：root 不存在 {root}")
        return 2

    if args.record_baseline:
        try:
            failures, collected, rc = runner(root, args.pytest_arg, args.timeout)
        except subprocess.TimeoutExpired:
            print(f"GATE-RED 录基线超时 {args.timeout}s——套件挂死，基线不落盘")
            return 1
        if not collected:
            print(f"GATE-RED 空跑/未正常收尾（collected={collected}，rc={rc}），"
                  f"基线不落盘——拿静默空跑当基线等于给后续放行造假绿通道")
            return 1
        write_baseline(baseline_path(root, args.baseline), failures, collected, root)
        print(f"GATE-OK 基线已录：{len(failures)} 条失败 / collected {collected}")
        return 0

    try:
        baseline = load_baseline(baseline_path(root, args.baseline))
    except ValueError as exc:
        print(f"GATE-RED {exc}")
        return 2
    if baseline is None:
        print(f"GATE-RED 无基线文件 {baseline_path(root, args.baseline)}——"
              f"先跑 python scripts/migration_gate.py --record-baseline")
        return 2

    try:
        failures, collected, rc = runner(root, args.pytest_arg, args.timeout)
    except subprocess.TimeoutExpired:
        print(f"GATE-RED 超时 {args.timeout}s：pytest 挂死按失败处理，不出子集结论")
        return 1

    # 磁盘核定（pytest 9 实测）：`-k` 打错→「2 deselected」+rc5；空目录→「no tests
    # ran」+rc5。rc5 与 collected==0 同判「静默空跑」——两种形态都绝不绿。
    if collected == 0 or rc == 5:
        print("GATE-SKIP-EMPTY collected 0——静默空跑（-k 打错/路径过滤？），"
              "对 pytest 同样成立的巡检点：空跑绝不绿")
        return 1
    if collected is None:
        print(f"GATE-RED pytest 未正常收尾（rc={rc}，无 collected 行）——先修 pytest 本身")
        return 1
    if rc not in (0, 1):
        print(f"GATE-RED pytest rc={rc}（2 中断/3 内部错/4 用法错/5 无测试）——失败集合不可信")
        return 1

    result = compare(set(baseline), failures)
    if result["resolved"]:
        print(f"改善 {len(result['resolved'])} 条（仍列出让失败集合收缩可审计）：")
        for node in result["resolved"][:20]:
            print(f"  RESOLVED {node}")
    if not result["subset_ok"]:
        print(f"GATE-RED 新增失败 {len(result['new_failures'])} 条——迁移引入的破损，逐条点名：")
        for node in result["new_failures"]:
            print(f"  NEW {node}")
        return 1
    print(f"GATE-OK 失败集合子集成立（current {len(failures)} <= baseline "
          f"{len(baseline)}，collected {collected}）")
    print("子集成立≠迁移成功，真机取证另跑（design_c3 §6：离线绿不可互相顶替，"
          "fake/真实 schema 漂移 gate 守不住）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
