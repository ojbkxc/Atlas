# 设计 C6：版本三元组写入报告契约（波次 W1，次序第三位）

> 上游：gap6 §3.1-C6、§4-C6、§9（`dsl.py:121` `FlowDocument.md5` 已备料、recorder/reporting/allure grep version 零命中、「只差落报告一步」属实、`knowledge.py:110` sha1 防 PYTHONHASHSEED 先例属实）。
> 实施者：impl_report（任务 #3 后半）。

## 1. 目标

每次 run 记录与 Allure 报告带三元组：**引擎版本 + 知识库指纹 + flow md5**。排查「上周能跑今天炸」先看三元组 delta；本机知识/引擎漂移从玄学变成可 diff 的事实。零新写路径（**只读**指纹，红线 #9/#10/#11 无涉）。

## 2. 新增文件与接口签名

### 2.1 `src/atlas/versioning.py`（新，唯一计算点）

```python
def knowledge_fingerprint(private_root: "Path | None") -> dict:
    """知识库指纹（sha1，防 PYTHONHASHSEED——knowledge.draft_pitfall 同款纪律）。
    private_root 有效（目录存在）→ 遍历目录内 `*.json`（rglob，一层/多层都算），
      条目 = f"{rel.as_posix()}:{sha1(文件字节)}"，全部条目排序后拼接再 sha1[:12]，
      {"sha1": ..., "source": "private", "files": n}
    private_root None/不存在 → 同法对包内 plugins/demo/knowledge/ 计算，
      {"sha1": ..., "source": "builtin", "files": n}
    目录存在但读崩（权限/坏字节）→ {"sha1": "", "source": "unreadable", "error": str}
    ——绝不裸崩（#64：诊断可以有噪音不能丢整份；doctor 的 _read_text_safe 先例）。
    rel 键用 as_posix() 排序：同内容两平台得同指纹（跨机可对比的前提）。"""

def flow_digest(flow: object) -> dict:
    """FlowDocument 且有 source_text → {"md5": doc.md5, "kind": "source"}（dsl 现成，不重算）；
    普通 dict（内存构造/overlay 丢形态）→ {"md5": sha1(json.dumps(sort_keys=True,
    ensure_ascii=False))[:12], "kind": "derived"}——derived 是降级不是假象，kind 明写。"""

def version_triple(private_root: "Path | None", flow: object = None) -> dict:
    return {"atlas": __version__,
            "knowledge": knowledge_fingerprint(private_root),
            "flow": flow_digest(flow) if flow is not None else None}
```

private_root 取源（实现期对齐磁盘）：`resolve_plugin().knowledge()` 的私有根属性（`KnowledgeBase(root, fallback_root)` 的 root，未设 env 为 None）；属性名以盘上为准，若无公开属性则回落 `os.environ.get("ATLAS_KNOWLEDGE_DIR")`——与 `plugins/demo/plugin.py:22-34` 同源，**不留第二份判据**。

### 2.2 `src/atlas/recorder.py`（改，零依赖纪律不破）

`RunRecorder.__init__(..., version: dict | None = None)` 新末位可选参；`close()` 记录构造处：`if self.version is not None: record["version"] = self.version`——缺省不写键，**存量 runs.jsonl 形态零涟漪**（#115），旧测试不翻。versioning import 不进 recorder（recorder 保持零依赖，version 由调用方传入）。

### 2.3 `src/atlas/dsl.py`（改一处，:757 附近）

run() 里创建 RunRecorder 时计算传入：
`recorder = RunRecorder(..., version=versioning.version_triple(private_root=_private_knowledge_root(), flow=flow))`。
flow 即当前参数（文件来的带 md5，内存的 derived）。

### 2.4 `src/atlas/allure_report.py`（改 `_run_to_result` :283）

parameters 从恒 `[{"name":"env",...}]` 扩为 env + `atlas_version` / `knowledge_sha1`（值含 source 后缀 `#private|#builtin|#unreadable`）/ `flow_md5`（值含 kind `source|derived`）三键，**仅当 `run.get("version")` 存在**（旧结果集行为逐字不变）。labels 不动（historyId 口径不掺版本，防跨版本趋势断裂）。

### 2.5 `src/atlas/cli.py` doctor 分支（impl_report 本波 cli 单主）

`checks["version_triple"] = {"atlas": __version__, "knowledge": {...指纹}, "tz_data": <zoneinfo 可用性——C1 前置体检，顺带落，见 design_c1 §7>}`；展示项，不改 rc 逻辑；unreadable 指纹只显式报 source=unreadable 不静默。

## 3. Schema 落盘形态

runs.jsonl 行新增（可选键）：
```json
"version": {"atlas": "0.1.0",
            "knowledge": {"sha1": "1a2b3c4d5e6f", "source": "private", "files": 30},
            "flow": {"md5": "9f8e…", "kind": "source"}}
```

## 4. 跨平台条款

- X1：rglob + as_posix 键排序——**同一知识库两平台指纹必须逐字相等**（专项测：造同内容双目录模拟，Windows 反斜杠混入键即破）。
- 已知限制（写进 doctor 展示注释与设计留档）：git autocrlf 若改变文件字节，指纹随字节变——三元组定位「本机今天 vs 本机上周」，跨机对比以 `source+files` 计数为辅。此为口径不是缺陷。
- X4：doctor JSON 输出 ensure_ascii=False 走既有 print 路径，无新增符号字符。
- X6：`tz_data` 体检 `zoneinfo.ZoneInfo("Asia/Shanghai")` 可用性（不可用报「Windows 需 pip install tzdata」），为 C1 排雷，归 C6 顺手落（cli doctor 同主）。

## 5. 测试清单（`tests/test_versioning.py` 新 + `tests/test_allure_report.py` 追加）

1. 指纹确定性：同目录两次计算相等；文件内容变一个字节 → sha1 变；新增/删除文件 → 变；键序乱序目录树 → 不变（排序生效）。
2. source 三态：private/builtin/unreadable 形态断言（monkeypatch env 与 tmp 目录）。
3. flow_digest：FlowDocument 走 md5（kind=source）；内存 dict kind=derived 且两次相等；overlay() 副本保留形态（md5 与父一致，防「解包丢形态」复发——dsl.overlay 注释同族）。
4. recorder 零涟漪：不传 version → 行内无键；传 → 键位如 schema；既有 recorder 测全过。
5. allure：无 version 旧行 parameters 恒 env（存量测不翻）；有 version → 三参数带 source/kind 后缀。
6. doctor：`version_triple` 键存在、atlas 版本号与 `__init__` 一致、tz_data 布尔。
7. 新增测下限 **≥12**；772 不回退。

## 6. 红线五查

`stdlib ✓（hashlib/json/zoneinfo）/ 断言 ✓ / 知识写路径 ✓（纯读）/ 依赖 ✓（tzdata 只体检不硬依赖）/ 离线 ✓`。红线 #11：指纹是数据不是引擎字面量；知识库版本**不用 git 资产版本冒充**（AGENTS 明说私有库是人手改的，git sha 盖不住——所以取目录内容指纹）。

## 7. 未验证/开放问题

- 「上周能跑今天炸」的真实案例回放未做（需要用户拿一条真实 runs.jsonl 前后对比）——交付时列为验收动作而非已验证。
- 私有目录在网盘/同步盘上的字节抖动会造成指纹噪音：口径=有噪音可解释（files+source 辅助），不修。
