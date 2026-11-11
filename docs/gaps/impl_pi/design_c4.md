# 设计 C4：凭据泄漏面治理（波次 W1，先行）

> 上游：gap6 §3.1-C4、§4-C4、§9〔E7/E8/E9〕；`research_pi/c4-atlas-exposure.md`（C4-INV 已核实）。
> 定级口径（已钉死，不得写反）：`D:\jmx` 实测——主凭据已 `${__P()}` 外部化，`isc-axios-sessionid` 全串出现数=Header 元素数=**582**，逐一核对 582 个全部为字面 UUID、零 `${}` 变量。「按行 802」「按元素约 400」口径作废。本项=**补字面敏感头照抄缺口（随时会泄）**，不是堵现行泄露事故。
> 实施者：impl_report（任务 #3 前半）。

## 1. 目标与范围

四条子项，按暴露面优先级：
- **C4①** 迁移器/生成器字面敏感头闸（名单+启发式）+ 无 `-o` 拒打全文；
- **C4②** 异常原文净化收敛进 `failures` 单一真相，一处覆盖三条落盘路径，堵 `runtime.py:175`；
- **C4③** Allure 分发 SOP（`--bind 127.0.0.1` 口径落到提示文案）；
- **C4④** `PRIVACY.md` 口径修正 + 每条 PRIVACY 红线配一条会红的哨兵测（M11 手法）。

不做（已排除，别当口子查）：`abilities.py` 打印（零 print）、ctx 落盘（recorder 只序列化 steps）、Allure 附件含 header（只有截图字节）。`save_har` 免责边界是有意取舍不动。

## 2. 新增文件与接口签名

### 2.1 `src/atlas/credential_gate.py`（新，唯一判据实现——M13「单一实现」纪律）

```python
SENSITIVE_HEADER_TOKENS: tuple[str, ...] = (
    "authorization", "proxy-authorization", "cookie", "set-cookie",
    "token", "access_token", "accesstoken", "refreshtoken",
    "password", "passwd", "secret", "api-key", "apikey", "x-api-key",
    "sessionid", "session-id", "jsessionid", "xsrftoken", "x-csrf-token",
    "ticket", "x-auth",
)
# 判据 = 键名命中名单（子串匹配，小写归一）∨ 值形态启发式；isc-axios-sessionid
# 这类业务前缀名靠 "sessionid" 子串命中——业务字面量不进名单（红线 #11 精神：
# 名单是通用 HTTP 凭据词表，不是 ICS 头清单）。
SENSITIVE_QUERY_KEYS: tuple[str, ...] = (
    "token", "access_token", "code", "ticket", "state", "password", "pwd",
    "secret", "jsessionid", "sessionid", "key", "apikey",
)

def looks_literal_secret(value: str) -> bool:
    """值形态启发式（确定可测，不猜）：字面且呈以下形态之一——
    UUID（8-4-4-4-12 hex）/ JWT（eyJ 前缀三段）/ ≥32 位 hex 或 base64 样连续串。
    `${...}`/`__P(` 开头的已外部化值恒 False（那是人审接 env 的位，不重复告警）。"""

def header_is_sensitive(name: str, value: str) -> bool:
    """键名命中 SENSITIVE_HEADER_TOKENS（子串）或 (值命中 looks_literal_secret 且键名含
    凭据语义子串)。纯函数，唯一判据；import_jmeter 与 emit_flow 只许调它。"""

def placeholder_for(index: int) -> str:  # -> "${ATLAS_HDR_%d}"

def sanitize_url(url: str) -> str:
    """剥 query 与 fragment：scheme://host[:port]/path?<redacted>。
    runtime.py:175 与异常净化共用（同一把刀，不复制第二份）。"""

def sanitize_text(text: str) -> str:
    """异常文本净化（C4② 的纯函数核）：
    1) `scheme://...` URL 的 query/fragment → sanitize_url；
    2) `(?i)(k)=[^&\s\"']+` 其中 k ∈ SENSITIVE_QUERY_KEYS ∪ 名单 → `<redacted>`；
    3) 裸 `Cookie: xxx` / `Authorization: Bearer xxx` 值段 → `<redacted>`。
    永不抛：入参非 str 时原样 str() 回落（定性器不得先于被定性对象死 #89）。"""
```

### 2.2 `scripts/import_jmeter.py` 改造（行号按现盘）

- `extract_samplers` 收集 headers 后**不**改结构；新增 `apply_header_gate(flow: dict) -> list[tuple[int, str, str]]`：遍历 `steps[*].headers`，`header_is_sensitive` 命中的值替换为 `placeholder_for(n)`，返回 `(步骤序号, header名, env建议键)` 清单（**不回显值**）。
- `to_atlas_dsl` 之后、`yaml.dump` 之前调用闸（闸在产出侧——M14「确认位必须在产出侧」）。
- stderr 清单头文案 `⚠` → `WARN:`（X4 ASCII 化，现 :181 的 `⚠` 在 GBK 控制台同样会崩，属存量修复）。
- **无 `-o` 且闸命中 >0**：拒打 YAML 全文；改为打摘要（步数/带头条数）+ 指引「字面凭据已占位；完整 YAML 请用 -o 写文件（flows/ 不在 gitignore，产物请放受控目录）」。无命中保持旧 stdout 行为（存量测 `test_import_jmeter.py` 不翻）。
- 映射输出形态：占位对应关系写 stderr 键名清单 `ATLAS_HDR_1 = 步骤2 header isc-axios-sessionid → 值放 .env`，**值永不进 stdout/stderr/YAML**。

### 2.3 `src/atlas/generator/emit_flow.py`（同形态闸，:38-44）

`_build_step` 内 `headers = dict(global_headers)` 并入后、`step["headers"] = headers` 之前调 `credential_gate.header_is_sensitive` 替换占位；`atlas gen` 汇总提示沿用 2.2 文案（`cli.py` `_gen` 分支由 impl_report 顺改 2 行 print，不新增参数）。

### 2.4 C4② 异常净化（单一真相，一处覆盖三条路径）

- `failures._exception_display(exc)` 内 `return sanitize_text(f"{exc}")` —— **只改这一处**：`exception_text`（定性+草稿）、`exception_message`（agent 留痕 :126）、engine `degraded_to_ui.reason` 全部经它派生（#92 不复制第二份）。
- `engine.py:252`：`{"task":…, "reason": str(exc)[:200]}` → `{"task":…, "reason": failures.exception_message(exc)[:200]}`（原来是绕过单一真相的裸 `str(exc)`，净化进不来的死角）。
- `runtime.py:175`：`URL={page.url}` → `URL={credential_gate.sanitize_url(page.url)}`（框架自己写 URL 进异常是确定行为，SSO 回调 query 常带 ticket/code）。
- **分类判据影响评估（评审点）**：`_RULES`/`_DATA_ABSENT` 关键词均为词/短语（timeout/401/无结果…），query 值与 token 段不含这些词；净化只动「=值/URL query」不动词序。trigger 多为异常类名，不受影响。**哨兵测必须钉**：净化前后 `classify_text` 结果全等（参数化样本集）。

### 2.5 C4③/④

- `PRIVACY.md:19` 改写为实态：steps 含 `degraded_to_ui`（**净化后**异常摘要）与 `dispatch`（业务取值，路由审计必需）、`attachment` 路径；ctx 仍不序列化；新增「字面凭据闸」与「异常 query 净化」两段承诺。分发段新增：Allure serve/`http.server` **必须 `--bind 127.0.0.1`**，`scaffold.py:160-173` 打印的 serve/open 命令补 `--bind 127.0.0.1` 提示（纯文案，子命令参数不动）。
- `tests/test_privacy_drift.py`（新，M11 哨兵）：每条 PRIVACY 承诺配一条会红的测——(a) ctx 不落盘：跑 fake flow 断言 runs.jsonl 无 ctx 键；(b) 敏感头不进产物：含 582 同款字面 sessionid 的最小 JMX 走 import → YAML/stdout 双断言 `b5360b2f` 形态 UUID 不出现、出现 `${ATLAS_1}`；(c) 异常净化：SSO 回调文案样本走 `exception_text` 断言 `?<redacted>`；(d) `degraded_to_ui.reason` 经净化。

## 3. 波及文件与归属

| 文件 | 改动 | 归属 |
|---|---|---|
| `src/atlas/credential_gate.py` | 新建 | impl_report |
| `scripts/import_jmeter.py` | 闸+stdout 拒打+ASCII | impl_report |
| `src/atlas/generator/emit_flow.py`、`src/atlas/cli.py`（_gen 2 行 print） | 闸接线 | impl_report |
| `src/atlas/failures.py`、`src/atlas/engine.py`（:252）、`src/atlas/runtime.py`（:175）、`src/atlas/scaffold.py`（serve 文案） | 净化收敛 | impl_report |
| `PRIVACY.md` | 口径修正 | impl_report |
| `tests/test_import_jmeter.py`、`tests/test_privacy_drift.py`(新)、`tests/test_failures_sanitize.py`(新) | 见 §6 | impl_report |

## 4. Schema 字段（产物形态变化）

- flow YAML `step.headers` 值域变化：敏感值 → `"${ATLAS_HDR_<n>}"` 字符串占位（DSL 变量插值既有机制承接，人审接 env 即可跑，`compile` 不报未解析占位符吗——`${` 是运行时插值语法，**compile 绿**，既有 `unresolved_placeholders` 告警路径照常点名，人审一步不缺）。
- `runs.jsonl` step 记录 `degraded_to_ui[].reason`：文本净化后 ≤200 字符，形态不变（不新增键、不动 recorder）。

## 5. 跨平台条款

- X1：新文件零反斜杠；X4：所有新 print 文案 ASCII 标记（WARN/OK），中文正文 GBK 可编；X5：不动 JSONL 写路径；净化正则不依赖换行符形态（`\r` 归入 `\s` 类，CRLF 样本入测试）。
- Windows 特有回归点：`yaml.dump(allow_unicode=True)` 写文件已 `encoding="utf-8"` 显式——不动。

## 6. 测试清单（离线，全 fake）

1. `header_is_sensitive`：authorization/cookie/token/x-api-key 命中；isc-app（值短、名不含词）不命中；`${__P(new_accessToken,)}` 不命中；`isc-axios-sessionid`+字面 UUID 命中（582 复现样本）。
2. `looks_literal_secret`：UUID/JWT/32hex 命中；`abc`、中文、纯数字流水号不命中。
3. import 端到端：带字面 sessionid 的最小 JMX → `-o` 产物含占位、stderr 键名清单、值 0 出现；无 `-o` → stdout 无 YAML 全文 rc 仍 0；无敏感头 → stdout 行为逐字不变（存量测零涟漪 #115）。
4. 净化：`RuntimeError("...URL=https://sso.example/cb?ticket=ST-123&code=abc")` → `?ticket=<redacted>` 形态；`classify_text` 净化前后等值参数化（≥8 样本，覆盖 `_RULES` 六类）；requests ConnectionError 文案样本（含 `?token=`）；Playwright navigating 文案样本（`[推断]` 面用 fake 钉死）。
5. 三路径贯通：journal 草稿 `evidence.text`、agent 留痕 `reason`、`degraded_to_ui.reason` 同异常同净化。
6. privacy 哨兵 (a)–(d)。
7. 新增测下限 **≥18**；772 不回退。

## 7. 红线五查与既有纪律

`stdlib ✓ / 不放宽断言 ✓（净化只擦秘密不擦失败词，且判据等值有测）/ 知识写路径 ✓（零新增）/ 依赖 ✓ / 离线 ✓`。#92 单一真相：闸一个实现（credential_gate）、净化一个实现（_exception_display）；E8 定性：本项是把 PRIVACY **既有承诺落成机制**，不是新开硬规则；红线 #8 截图面不动。

## 8. 未验证/开放问题（C16）

- Playwright 导航超时文案带 query 仍属 `[推断]`，fake 样本能钉净化行为，钉不了真实文案完备性——真机取证留给用户按 SOP（读 reports/ 产物 grep `?<redacted>` 是否足量）。
- `sanitize_text` 对**非 URL 上下文的裸高熵串**（如报文里夹 token 值无键名前缀）不处理——那是 body 面，超出 C4 范围（PRIVACY 已声明完整请求体默认不写日志），评审时不得被扩成「全量脱敏」。
