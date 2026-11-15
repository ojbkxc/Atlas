"""把 Atlas 运行记录转换为外部 runlog 报告格式；不依赖其安装。"""
from __future__ import annotations
import json
import os
from collections import Counter
from pathlib import Path

from .atomic_io import read_text_retry
from .failures import FLOW_STATUS_TIMEOUT, FailureClass, is_operational


# en-8 读侧：写侧 `ATLAS_RUNS_SPLIT=plugin` 把记录分流到 `runs.<plugin>.jsonl`
# （recorder._split_runs_path，插件名单一来源 resolve_plugin().name）。读侧必须能把这组
# 分片当一个逻辑输入聚合，否则 summary/allure 只看到默认 `runs.jsonl`（可能根本不存在）
# 或只看到最后写入的那个平台——正是 en-8 要消除的「多平台混写互相污染」反向面。
# 判据与写侧**同一把尺**：只认精确值 "plugin"（默认关的开关不因近似拼写启用）。
RUNS_SPLIT_ENV = "ATLAS_RUNS_SPLIT"


def runs_inputs(path: Path) -> list[Path]:
    """把「一个逻辑 runs 输入」解析为实际要读的文件清单（en-8 读侧单点）。

    - 未设置 ``ATLAS_RUNS_SPLIT=plugin``（含设置成别的值/空）：返回 ``[path]``，
      逐字节旧行为（默认路径零涟漪）。
    - 设置时：返回同目录下全部 ``<stem>.*<suffix>`` 分片（排序保证确定性，与输入行序无关）。
      目录里若一个分片都没有（老的单文件场景），**诚实回落** ``[path]``——不伪造「无记录」，
      也不因 opt-in 了分流就读不到历史单文件。
    """
    if os.environ.get(RUNS_SPLIT_ENV) != "plugin":
        return [path]
    shards = sorted(
        candidate for candidate in path.parent.glob(f"{path.stem}.*{path.suffix}")
        if candidate != path
    )
    return shards or [path]


def _read_runs_text(path: Path) -> str:
    """读一份 runs 文件的原文；#131 读侧纪律：坏字节（腰斩多字节字符）回落 replace。

    cc-2 配套：``read_text_retry`` 只重试瞬态 PermissionError（写锁区间=文件头 1 字节），
    UnicodeDecodeError 不重试直接回落 replace（坏字节是确定事实），其余异常原样传播。
    """
    try:
        return read_text_retry(path, encoding="utf-8")
    except UnicodeDecodeError:
        return read_text_retry(path, encoding="utf-8", errors="replace")


def _read_inputs_text(inputs: list[Path]) -> str:
    """读取一组 runs 输入并按行拼接（en-8 读侧聚合）。

    单文件（默认路径）行为与旧的「一次 read_text」逐字等价：直接返回该文件原文。
    多分片时逐份读取、各尾部补一个换行再拼——分片最后一行没有尾换行（中断腰斩残行正是
    这种形态）时，两块直接相接会把两条 JSON 黏成一行，既有残行计数与合法行都会错判。
    """
    existing = [p for p in inputs if p.exists()]
    if not existing:
        # 与旧 `read_text_retry(path)` 的缺文件口径一致（FileNotFoundError），不静默写空导出。
        raise FileNotFoundError(f"运行记录不存在：{inputs[0]}")
    if len(existing) == 1:
        return _read_runs_text(existing[0])
    return "".join((_read_runs_text(p) + "\n") for p in existing)




def _version_shapes(run: dict) -> tuple[str, str, str] | None:
    """一条 run 的三元组形态（C6 读侧）：`atlas` / `sha1#source` / `md5#kind`。

    无 `version` 键（旧 runs.jsonl）返回 None——旧记录不冒充「已核对版本」。
    坏形态（键在但值不是映射）同样按 None 处理：聚合器不得因一条手改坏的记录崩掉整份
    报告（#126/#131 同纪律），也不得拼出 `None#None` 这种假指纹。
    """
    version = run.get("version")
    if not isinstance(version, dict):
        return None
    def _shape(value: object, fields: tuple[str, str]) -> str:
        if not isinstance(value, dict):
            return ""
        first, second = (str(value.get(f) or "") for f in fields)
        if not first:
            return ""
        return f"{first}#{second}" if second else first
    atlas = str(version.get("atlas") or "")
    knowledge = _shape(version.get("knowledge"), ("sha1", "source"))
    flow = _shape(version.get("flow"), ("md5", "kind"))
    if not (atlas or knowledge or flow):
        return None
    return atlas, knowledge, flow

def summarize(path: Path) -> dict:
    """按用例聚合：状态计数、失败类分布、最慢步骤。

    只读 JSONL，缺失文件明确报错，不静默返回空报告冒充“全部通过”。
    en-8：输入经 ``runs_inputs`` 解析——opt-in 分流时把同目录各平台分片当一个逻辑输入
    聚合（`summarize(runs.jsonl)` 在 split 模式下会覆盖 `runs.demo.jsonl` 等分片）。
    """
    inputs = runs_inputs(path)
    if not any(p.exists() for p in inputs):
        # 单文件传统路径的报错口径原样保留（第一分片即 path 本身）；分流时若一个分片都
        # 不存在也落这里——「没有任何记录」与「文件被删」都是需要人看见的事实，不静默。
        raise FileNotFoundError(f"运行记录不存在：{path}")
    status, fail_classes, slowest = Counter(), Counter(), []
    cases, runs, skipped = set(), 0, 0
    # C6 读侧：三元组分维度去重计数。排查「上周能跑今天炸」= 看哪个维度在这份 JSONL 里
    # 出现了 >1 个值（drift），而不是靠人回忆上周装过什么、改过哪条知识。
    versions: dict[str, Counter] = {"atlas": Counter(), "knowledge": Counter(), "flow": Counter()}
    runs_without_version = 0
    # #131：与 review.rows/_read_queue/compile_trace 同防线（#112/#109 纪律）——录盘
    # 中断可把多字节字符腰斩在字节上，旧 path.open(encoding="utf-8") 迭代整文件裸
    # UnicodeDecodeError：atlas summary 一行 stderr + rc2 口径失效（CLI 只捕 OSError/
    # ValueError）变裸 traceback，而 docstring 承诺「不静默返回空报告」的残行计数防线
    # 一行没轮到。先严格读保真，撞坏字节回落 errors="replace"：残行 JSON 解析必败，
    # 自然落进既有 skipped 计数分支，合法行照常聚合。
    # cc-2 读侧配套：runs.jsonl 的写锁区间=文件头 1 字节（与 recorder 同区间=同一把锁），
    # Windows 上并发读从字节 0 起必撞锁 → PermissionError（POSIX flock 咨询锁不挡读，无此
    # 问题）。锁持有是「一次整行写的毫秒级瞬态」，read_text_retry 重试即过。判据（与
    # atomic_io 同一把尺，#92）：**只重试 PermissionError**（瞬态锁冲突）；UnicodeDecodeError
    # 不重试、直接回落 replace 读入（坏字节是确定事实，重试无用）；其余异常原样传播（点名
    # 失败，不吞）——不是「加大重试次数」凑过，是「什么可重试、什么必须点名」的判据。
    raw = _read_inputs_text(inputs)
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            run = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1  # 崩溃时可能写了一半的行；计数上报而非静默
            continue
        if not isinstance(run, dict):
            # 合法 JSON 但非对象（裸串/数字/null）：与残行同等计入，不崩整份聚合。
            # 与 export_runlog 的容错保持一致——两个聚合器对任意 JSONL 都不得整体失败。
            skipped += 1
            continue
        runs += 1
        cases.add(run.get("case", ""))
        status[run.get("status", "unknown")] += 1
        shapes = _version_shapes(run)
        if shapes is None:
            runs_without_version += 1
        else:
            for dimension, value in zip(("atlas", "knowledge", "flow"), shapes):
                if value:
                    versions[dimension][value] += 1
        steps = run.get("steps", [])
        for step in steps if isinstance(steps, list) else []:
            if not isinstance(step, dict):
                continue   # steps:[null] 等外部/手改坏元素不得顶掉整份聚合（同 run 级容错口径）
            if step.get("fail_class"):
                fail_classes[step["fail_class"]] += 1
            dur = step.get("dur_ms", 0)
            if not isinstance(dur, (int, float)) or isinstance(dur, bool):
                dur = 0   # 非数值 dur_ms 不得让后续 slowest.sort 抛 str/int 比较错
            slowest.append((dur, run.get("case", ""), step.get("step", "")))
    slowest.sort(reverse=True)
    drifted = [dimension for dimension, counter in versions.items() if len(counter) > 1]
    return {"runs": runs, "cases": len(cases), "status": dict(status),
            "fail_classes": dict(fail_classes),
            "skipped_malformed_lines": skipped,
            # 三元组漂移视图：drifted 非空 = 这份记录里同一维度出现过多个值（知识被手改过/
            # 引擎换过版/flow 文件变过），正是「上周能跑今天炸」的第一手证据。
            "versions": {"atlas": dict(versions["atlas"]),
                         "knowledge": dict(versions["knowledge"]),
                         "flow": dict(versions["flow"]),
                         "drifted": drifted,
                         "runs_without_version": runs_without_version},
            "slowest_steps": [{"dur_ms": d, "case": c, "step": s} for d, c, s in slowest[:10]]}

def run_attention(summary: dict, tower_stats: dict | None = None) -> dict:
    """「这批跑里有多少在等人、有多少属操作/环境族」的聚合位（design_c9 §3 reporting 行）。

    为什么单独成函数而不是塞进 `summarize()`：summarize 的 status Counter 天然容纳新终态
    取值（#115，本体零改动），而「等人」还需要**审核塔积压**这一外部事实才有意义——
    批量巡检的「失败很多」里混着 20 条等人工步骤时，处置动作是找人，不是查 bug。
    家族成员判据走 `failures.is_operational` 单点（#92：本函数不留第二份名单）。

    `tower_stats=None` 表示**没人读过塔**：此时 `tower_pending` 是 None 且 notes 记
    `TOWER-NOT-READ`，绝不填 0——「没查过」和「查了是 0 条」是两回事（#64 诚实面）。
    输入形态坏（非映射/计数非数值）不抛：判据/聚合函数不得先于被聚合对象死（#89 家族），
    坏值按 0 计、原样给出可见键，不静默丢弃整块结论。

    flow 级 `status:"timeout"`（supervisor 强杀后补记的新终态）**不计入 operational**：
    它不属 OPERATIONAL 家族（评审必改 C9-E 判定），也不是等人。但它**单独成键可见**
    （`timeout`）——此前代码里只有「不入家族」的声明和负向钉，没有任何正向落点，
    超时的 run 在这个块里彻底隐身，读 summary 的人看不到「有多少条是被看护掐掉的」。
    归属口径见 failures.FLOW_STATUS_TIMEOUT 注释。
    """
    rows = summary.get("status") if isinstance(summary, dict) else None
    counts = rows if isinstance(rows, dict) else {}

    def _count(key: str) -> int:
        value = counts.get(key, 0)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return 0   # 手改坏的非数值计数按 0 处理，不猜也不崩
        return int(value)

    waiting = _count(FailureClass.WAITING.value)   # 取值走枚举，不在这里散写字面量（#92）
    operational = sum(_count(key) for key in counts if is_operational(key))
    timeout = _count(FLOW_STATUS_TIMEOUT)
    notes: list[str] = []
    if tower_stats is None:
        tower_pending = None
        notes.append("TOWER-NOT-READ")
    else:
        pending = tower_stats.get("pending") if isinstance(tower_stats, dict) else None
        tower_pending = pending if isinstance(pending, int) and not isinstance(pending, bool) else 0
    if timeout:
        # 超时要人看的是**看护配置与用例耗时**，不是缺陷列表——显式提示处置方向
        notes.append(f"TIMEOUT-COUNT={timeout}（看护掐表，查 --case-timeout 与用例耗时）")
    return {"waiting": waiting, "operational": operational, "timeout": timeout,
            "tower_pending": tower_pending, "notes": notes}

def export_runlog(input_path: Path, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # #131：同 summarize——一条腰斩坏字节曾让整份导出裸 UnicodeDecodeError，
    # CLI 的 rc2 友好口径（只捕 OSError/ValueError）失效变 traceback；
    # replace 读入后残行 JSON 解析必败，自然落进既有「跳过残行」分支。
    # en-8：输入经 runs_inputs 解析——opt-in 分流时把同目录各平台分片当一个逻辑输入导出。
    raw = _read_inputs_text(runs_inputs(input_path))
    with output_path.open("w", encoding="utf-8") as target:
        for line in raw.splitlines():
            if not line.strip():
                continue
            try:
                run = json.loads(line)
            except json.JSONDecodeError:
                continue  # 崩溃残行
            # 合法 JSON 但不是对象（裸串/数字/null），或缺外部 runlog 强制主键 run_id：跳过该行。
            # input 来自 CLI 用户传入，可能是外部/过滤后的 jsonl；一条坏行绝不能顶掉整份报告。
            if not isinstance(run, dict) or "run_id" not in run:
                continue
            run_id = run["run_id"]
            steps = run.get("steps", [])
            for step in steps if isinstance(steps, list) else []:
                if not isinstance(step, dict):
                    continue   # steps:[null] 等坏元素跳过，不顶掉整份报告（同 run 级容错口径）
                # 审计（2026-11-05，两批）：reloads 与 wait_ms/ui_ms 同形态死字段——全仓
                # 无任何生产方写这些键进 runs.jsonl，导出恒 0 就是假统计（红线 #7 同族）。
                # reloads：Atlas 没有浏览器 reload 统计概念（runtime._wait_topbar 只在
                # 登录兜底时 reload 页面，从不落任何步骤记录）；wait_ms/ui_ms：recorder 的
                # step 记录只有 step/op/ok/dur_ms/fail_class/attachment/degraded_to_ui 等键，
                # 耗时分解从无生产方。三个字段连同消费处一并删除；将来真做等待/UI 耗时
                # 统计，先接生产方再重新入 schema。api_ms **有**真实生产方
                # （engine.py:102 actor.api.last_timing_ms），保留。
                # retries：真实重试源**有三处**（旧注释只写 heal 一处，是 gap-fill 波长出
                # flaky 腿、C12 长出有界降级之后的口径失真——注释与盘上行为互为哨兵，失真
                # 的注释比没有注释更误导人以为「导出恒 0 就是没重试过」）：
                # ① heal 自愈：只读 Task 命中已审核知识后就地重跑恰好一次（engine.py:311-327，
                #    成功时写 record["healed"]=True，engine.py:324——真实形态由
                #    tests/test_heal_and_dsl_extensions.py 钉死）；healed=True ⇔ 恰 1 次，
                #    由实据推导而非捏造；自愈重试**失败**时 engine 不留 healed 痕迹
                #    （与「从未重试」不可区分），如实回落 0 不编数字；
                # ② 步骤级 flaky 重试（config.step_retry>0 才开）：engine.py:236-290，成功时
                #    写 record["retried"]=实际重试次数；
                # ③ 有界 auto 降级重执行：engine.py:_perform 的 (NotImplementedError,
                #    RuntimeError) 降级腿（现盘 :428 起），API 失败后走 UI 一次，留痕写
                #    record["degraded_to_ui"]——C12 收窄后仅「证明一个请求都没发出」才可走。
                # 导出字段口径**不变**（红线 #115 不把正常路径翻成错误）：外部/手改 jsonl 显式
                # 带 retries 键时原样透传（不覆盖他方生产方），否则读生产方真实键。
                # 2026-11-06 修 dead-read（评审 C12-C 独立缺口，lead 放行）：②的计数落在
                # `retried`（engine.py:294 现盘），本导出过去只读 `retries`，全仓零生产方写这个
                # 键——flaky 重试条数在 runlog 里**恒 0**，且旧注释把它登记成「遗留」继续原样
                # 读，等于让一个永不命中的分支冒充已实现口径。现按 retries(外部显式)→
                # retried(本引擎)→healed 推导三级回落，字段名与导出 schema 一律不改。
                if "retries" in step:
                    retries = step["retries"]
                elif "retried" in step:
                    retries = step["retried"]
                else:
                    retries = 1 if step.get("healed") else 0
                target.write(json.dumps({"run_id": run_id, "case": run.get("case", ""),
                    "env": run.get("env", ""), **{key: step.get(key, 0) for key in
                    ("step", "op", "ok", "dur_ms", "api_ms")},
                    "retries": retries,
                    "fail_class": step.get("fail_class")}, ensure_ascii=False) + "\n")
    return output_path
