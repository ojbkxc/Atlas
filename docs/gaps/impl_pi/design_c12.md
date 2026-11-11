# 设计 C12：写不盲重试（投递不确定不自动重试，波次 W5）

> 上游：gap6 §3.2-C12（价值=中、与红线 #14 配额先占同源取增量；红线列 `✓·✓·✓·✓·✓`；pi=P19「Failed input is not retried because its delivery may be ambiguous」terminal.md:39）+ §5 交叉验证表「判据单一真相」。
> 磁盘基线（2026-11-06 实测核盘，本设计的事实前提）：
> - 现防线：`healing.can_auto_heal`/`retry_read_only` 拒非只读重试（healing.py:47-48/72）；`engine._perform` 捕获集只含 `(NotImplementedError, RuntimeError)`，OSError 系网络故障原样传播（engine.py:274，既有测 `test_plugin_and_legacy.py:328-339` 钉「网络故障不被降级掩盖、ui_called is False」）。
> - **真口子（本设计要堵的）**：`engine._perform`（engine.py:269-284）在 API 尝试**可能已经把请求发出去之后**才捕获 `RuntimeError/NotImplementedError` 并降级重跑 UI——写任务（`read_only=False`，core.py:28）+ `channel=auto` + `supports_api` 形态下 = 同一写操作第二次执行 = 双单。该形态**不是假想**：`plugins/demo/tasks.py:185-186` 请求已发出后仍 `raise NotImplementedError("API 查客户失败…")`（只读接口无害，写接口同构即炸）；`DemoTask._perform_api` 的「禁止写操作走 auto」（tasks.py:146）是**注释约定，无机制**——#106/#86 同族（文档承诺≠接线）。
> - `reporting.py:136` 注释钉着「唯一真实重试源是 engine heal 模式」——C12 落地后这句须扩为「heal 重试 + **有界降级重执行**」，两处同波改（判据注释漂移=#92/#102 家族，测试钉）。
> 实施者：impl_c12（任务 #4；W5 单主，见 design-plan-p2 §2）。基线 1448 passed 不回退。

## 1. 判据单一真相放哪（拍板答案）

对齐 C4 `credential_gate.py` / C6 `versioning.py` / C5 `boundaries.py` 先例——**一道闸一个小模块**：新 `src/atlas/delivery.py`。

```python
# src/atlas/delivery.py（新，纯 stdlib：hashlib/json）
IDEM_WHERE_KEY = "atlas_idem"          # 通用机制键名唯一出处（红线 #11：非业务字面量）
TOKEN_HEX_LEN = 16                      # sha1 截断长度；形态约束见 §4（避开净化刀判据）

def canonical_json(value: object) -> str:
    """排序键+UTF-8+无空格的稳定序列化（where 键序不得影响 token）。坏形态点名 ValueError，不猜。"""

def idempotency_token(entity: str, where: dict, scope: str = "") -> str:
    """sha1(canonical(entity|scope|where))[:16]。确定性⇒跨 run 同条件同 token——
    下次 find/人工追单的关联把手。scope 由调用方注入（如 flow id），Atlas 不猜作用域。"""

def safe_to_auto_reexecute(*, read_only: bool, attempted: bool, exc: BaseException) -> bool:
    """「同一操作被自动第二次执行是否安全」的唯一判据（真值表见 §2）。
    消费方只许调它：engine 降级闸（§3.2）；healing 维持既有门不改用（§3.6）。"""
```

## 2. 真值表（判据钉死，逐行成测）

| read_only | attempted（请求已开始发出） | 异常 | 结果 | 依据 |
|---|---|---|---|---|
| 任意 | 任意 | `isinstance(exc, failures.CONTROL_FLOW)` | **False** | #108：终止信号不重跑，CONTROL_FLOW 单一真相（#114）在本判据内委托，不留第四份 tuple |
| True | 任意 | 任意非控制流 | True | 只读无副作用，重试安全（现状 heal/降级语义不变，#115 不翻正常路径） |
| False | **False**（证明一个请求都没发出） | 任意非控制流 | True | 重跑等价于首次执行（未发出=无投递） |
| False | **True**（已发出，结果未知） | 任意非控制流 | **False** | gap6 C12 主红线：**绝不把写当只读自动重试（#9）** |

`attempted` 未知（无插桩面，见 §3.2）→ 调用方**按 True 传**（fail-closed：无法证明没发出，就视为已发出）。真值表 4 行 + 两未知分流 = 参数化测 8 组起。

## 3. 接线（文件级，签名级）

### 3.1 `src/atlas/abilities.py`（改，插桩点）

`CallApi.__init__` 增 `self.request_seq = 0`；`request()`（abilities.py:99）**函数体首部、组包校验之前**自增。口径：「attempted = 请求进入发送路径」，不是「服务端已收到」——保守方向覆盖 502/断连/半包全部歧义态。requests 默认 `HTTPAdapter` 无重试（`max_retries=0`）——**测钉**：断言 CallApi 装配未设 Retry、源码扫无 `max_retries` 赋值；「以后引重试库」由该测变红拦（防线前置，不靠人记）。

### 3.2 `src/atlas/engine.py`（改 `_perform`，:269-284）

```python
if task.metadata.channel == Channel.API or (... and getattr(task, "supports_api", False)):
    seq_before = actor.api.request_seq if CallApi in actor.abilities else None  # demo_api 等未插桩= None
    try:
        return self._perform_via_api(actor, task)
    except (NotImplementedError, RuntimeError) as exc:      # 捕获集不变（OSError 传播红线不动）
        if task.metadata.channel == Channel.API:
            raise                                          # api 通道无兜底，原语义
        attempted = True if seq_before is None else (actor.api.request_seq != seq_before)
        if not delivery.safe_to_auto_reexecute(
                read_only=task.metadata.read_only, attempted=attempted, exc=exc):
            raise failures.DeliveryUncertain(
                f"投递不确定：API 尝试已发出请求{seq_note}且任务非只读，"
                f"禁止自动降级重跑；结果请人工核实后再决定是否重放："
                f"cause={type(exc).__name__}") from exc    # 原异常作 __cause__ 保留（#90 口径）
        # seq_note := ""（无插桩面）或 f"（seq {seq_before}->{actor.api.request_seq}）"
        # ——拼串只在 seq_before 非 None 时触碰 actor.api（CallApi 在场性已证，防 demo_api 形态 AttributeError）
        actor.ctx.setdefault("_auto_degraded_to_ui", []).append(
            {"task": task.name, "reason": exception_brief(exc)[:200]})      # 原路径逐字保留
```
- 只读任务降级、`seq_before` 未变的写任务降级（NotImplementedError 于发送前）、OSError 传播——**三形态行为逐字不变**（#115：判据收紧不得把正常路径翻成错误）。
- 执行器失败路径（engine.execute :126 之后）对 `DeliveryUncertain` **不做控制流式短路**：它照常走坑点草稿/取证/归因链（真失败，不豁免——与 C9 waiting 的分界见 design_c9 §2），仅在 except 块内一处 `isinstance(record, dict)` 判定后写布尔键 `record["delivery_uncertain"] = True`（判据来自类型非字符串；与 healed 就地更正同协作口径 engine.py:169-172，不新开 step）。
- heal 重跑腿（:141）零改动：`can_auto_heal` 的 read_only ∧ approved 知识 ∧ CONTROL_FLOW 拒绝已是最严形态，C12 **不给 heal 新增任何「探测通过再重试」通道**。
- `channel==API` 通用 POST 写任务无降级腿（raise 即止）——不变，设计内注明防评审误读。

### 3.3 `src/atlas/failures.py`（改，W5 段——W6 在同文件续改，跨波单主串行见 design-plan-p2 §2）

```python
class DeliveryUncertain(RuntimeError):
    """请求已发出、投递结果未知：不是策略阻挡（≠EnvBlocked），不是代码缺陷归因。
    继承 RuntimeError 是有意：任何仍按 RuntimeError 处理的旧消费方不被翻成裸崩（#115），
    新消费方按 isinstance 精判。"""
```
`classify_failure` 增 isinstance 短路（置于 CONTROL_FLOW 之后、`classify_text` 之前）→ `FailureClass.INTERACTION`：网络交互故障族，**不进** ENV_BLOCKED（不是策略阻挡）**不落** unknown（污染缺陷分布，#114 面 A 同红线）。**关键词表 `_RULES` 一字不动**——文本侧永不产出该归类（外部文本不猜投递状态，与 C9「关键词不造假 waiting」同纪律）。

### 3.4 `src/atlas/probes.py`（幂等预生成落点）

- `DataProvider.supply`（:181-220）：`quota.reserve` **之前**算 `token = delivery.idempotency_token(entity, requirement.where, scope)`；
  - 只读策略腿不变：`find` 命中即返回——**「写操作先探测再决策」的 Atlas 既有形态本来就是 find_or_create 的 find 先腿**（supply:187-190），C12 不改这条腿，只补测钉「find 命中 ⇒ create 调用次数 0」；`find_only` 无数据→EnvBlocked **不造单不占额**（probes.py:191-193 实测在盘）——既有行为钉桩，非新增。
  - `CreateQuota.reserve(entity, document, idem: str = "")`：**给了才写键**（idem 非空才在条目加 `"idem"`——C6 version 参「缺省不写=存量形态零涟漪」先例）；读侧 `item.get("idem")` 容错，旧行无键不崩（#112）。`record_document` 更新 document 不动 idem 键。
  - `probe.create` 抛任何异常：**原异常原样传播（真因优先，#82），不包装不吞**；quota 条目已含 token 与 where——回收清单即证据。
- `CreateQuota.save_for_recycle`：条目原样序列化（新键自然进 `recycle_list.jsonl`），写侧合并/读侧容错纪律一字不改。
- **与 #14 配额先占的关系（gap6「同源取增量」的落点）**：#14 保证「超额不建单」（调用前占用），C12 保证「失败不盲重发」——两条腿合成闭环：同 run 内写投递不确定后，任何自动再触发都被已占配额拦成 EnvBlocked（**测钉**：fake creator 第一次抛 ConnectionError 第二次本可成功 → creator 实调 1 次、第二次 EnvBlocked、回收行含 idem）；跨 run 靠确定性 token 供 find/人工关联。配额先占语义零改动，engine `validate_write`（config.py:198-204）零改动。

### 3.5 `src/atlas/creators.py`（幂等键形态到脚本面）

- `ScriptedCreator.__init__(..., honors_idempotency: bool = False)`（probes.py:157 同步签名）；`make_scripted_creator(..., honors_idempotency: bool = False)` 透传。
- False（缺省）：**进脚本 stdin 的 JSON 逐字节形态不变**（不往脚本不认识的键塞东西——红线 #12 不给未验证能力加功能面）；token 仅落配额/回收台账。
- True（人声明脚本兑现该键）：`where = {**where, delivery.IDEM_WHERE_KEY: token}` 进 stdin；脚本侧以该键做自身去重（业务实现归业务，引擎只传递——红线 #11）。
- `require_audit_tag` 零改动。

### 3.6 `src/atlas/healing.py` —— **零改动**（「只读面不扩」的答案）

「非只读禁自动重试」的既有执行点就是 healing 的两道 `PermissionError`（:47-48/:72）+ engine heal 门；C12 的增量全在 **engine 降级腿与 supply 台账**，不给 healing 新增任何接口/参数/重试分支。补测钉现状（若缺）：`retry_read_only` 对 `read_only=False` 抛 PermissionError、`can_auto_heal(写任务)=False`——把「只读面」从行为约定升级成回归断言。

## 4. Schema 增量（runs.jsonl / recycle_list.jsonl）

```jsonc
// runs.jsonl step 记录（仅写降级闸命中时新键；旧记录形态零变化）
{"step": "...", "ok": false, "fail_class": "interaction", "delivery_uncertain": true}
// recycle_list.jsonl 行（条目级新可选键）
{"entity": "SalesOrder", "document": {...}, "idem": "1a2b3c4d5e6f7a8b"}
```

**token 形态约束（钉桩）**：16 hex——**刻意避开** `credential_gate.looks_literal_secret` 的 ≥32 hex/UUID 判据，保证 `sanitize_text` 不吞关联把手（对抗样本进测：含 32-hex 的异常被净化、含 16-hex token 的落盘字段存活）；PRIVACY facts 行扩两键（`delivery_uncertain`/`idem`，均无秘密——值派生自业务条件非凭据），`test_privacy_drift` 字段枚举同步（W5 归 impl_c12，见 design-plan-p2 §3）。

## 5. 测试清单（新 `tests/test_delivery_retry.py`；下限 **≥24**）

1. 真值表 §2 逐行参数化（含 CONTROL_FLOW 委托、attempted 未知→True 分流）。
2. `canonical_json`：键序无关、嵌套、坏形态点名。
3. `idempotency_token`：同参两次相等；entity/scope/where 任一变→变；长度 16 且全 hex。
4. `request_seq`：requests 层抛 ConnectionError 仍 +1（发送前自增）；未调用不涨。
5. engine：写+attempted RuntimeError → `DeliveryUncertain`、`ui_called False`、step 记录 `delivery_uncertain true`、`__cause__` 保留（#90）。
6. engine：写+未 attempted NotImplementedError → 降级恰 1 次、degraded_to_ui 键在（既有键语义不动）。
7. engine：读+attempted → 照常降级（#115 正常路径不翻）。
8. engine：KI 在 API 尝试中 → 传播 interrupted（#108 面扩到降级腿）。
9. engine：无 CallApi 能力（demo_api 形态）→ attempted 视为已发出 → 写任务不降级。
10. heal：`can_auto_heal(写)=False`、`retry_read_only(写)` PermissionError（钉「不扩只读面」）。
11. `classify_failure(DeliveryUncertain)=INTERACTION`；`classify_text("delivery uncertain")=UNKNOWN`（文本不造归因）；既有 8 类矩阵测零翻红。
12. supply：find 命中→create 0 次；find_only 缺数→EnvBlocked 且配额不占（钉既有）。
13. supply：create 抛错→配额仍占、回收行含 idem、原异常类型不变。
14. 同 run 双触发→第二次 EnvBlocked（creator 调用计数=1）。
15. `honors_idempotency` False→脚本 stdin JSON 与旧形态逐字节相等；True→含 `atlas_idem`。
16. 净化对抗：token 段穿越 `exception_brief`/`sanitize_text`；32-hex 样本被 redact（证明形态选择）。
17. requests 无隐式重试断言（§3.1 防线）。
18. X4：新 stderr/消息可 cp936 直断（返回串纪律，memory X4 教训：哨兵扫不到返回串）。
19. `test_plugin_and_legacy.py` 既有降级/heal 全组不回退（compat 见 §6）。

## 6. 前后兼容表（既有测预期）

| 既有测面 | 现状断言 | C12 后 | 结论 |
|---|---|---|---|
| test_plugin_and_legacy.py:102-126（降级） | read_only 缺省 True 的 fake | 走「读+未 attempted」行 | 绿，逐字不变 |
| :230-273（heal 重试+降级记录） | 只读 | 不变 | 绿 |
| :279-339（OSError/契约不降级） | 传播 | 捕获集未扩 | 绿（**红线测**，改动不得翻红它） |
| test_engine_and_dsl / test_heal_and_dsl_extensions | 8 类归因/终态 | DeliveryUncertain 新类型不在其输入集 | 绿 |
| test_probes / test_demo_probes / test_creators | supply 形态、stdin JSON | 缺省参不改形态；quota 条目加键 | 绿；若有条目 shape 全等断言 → 实现期列协调补丁（断言**增广**不放宽） |
| test_recycle.py:111-120（'?' 降级显示） | document 坏形态 | idem 为可选顶层键 | 绿 |
| reporting.py:136 注释「唯一重试源」 | 文档口径 | 扩为「heal + 有界降级」 | 同波改注释 + 行为测钉（防口径漂移=#92） |

## 7. 红线五查与红线自证

`stdlib ✓（hashlib/json）/ 断言：纯拦截，既有判定零放宽（#7）✓ / 知识写路径 ✓ 零新增（#9/#10）/ 依赖 ✓（不引重试库、不引 idempotency 框架）/ 离线 ✓`。
- **#9 重点自证**：读写分流判据**只取显式 `TaskMetadata.read_only`**（gap6 C12「须复用既有 `supports_api` 显式能力」的对应落点：API/UI 路由入口判据 `supports_api`（engine.py:271）一字不改，重试安全判据取 `read_only`——两者都是既有**显式声明能力**），绝不从 intent/channel/api_operation 动词猜——猜即是判据错面；残留面=作者把写谎标只读，属人审/`atlas tasks` 元数据审计范畴（#13/#10 塔前纪律），代码面不越权代判（红线 #11 不写业务启发式）。
- **#14 关系**：配额先占零改动，§3.4 写死合成语义。
- **`--approve` 不碰**：本设计无任何批准/豁免通道（红线 #13 面）；生产护栏 `validate_write` 位置语义逐字不动。
- **#7 面**：投递不确定失败**保持 failed**（fail_class=interaction + delivery_uncertain 标记），不借任何终态洗绿——与 C9 waiting 的边界见 design_c9 §2 非触发清单。

## 8. 跨平台条款（X1–X9，继承 design-plan §4）

X1：本项无新路径字面量；token 纯字符串。**X2**：creators 子进程 argv 形态零改动（既有 `[python, str(script)]` 合规），注入键走 stdin JSON 不进命令行。**X4**：新 stderr 段无 `↔ ✓ ⚠ █`（消息用「->」ASCII；哨兵自动入网 + §5.18 直断）。X5/X6/X7/X8/X9：不触（无新状态文件、无信号、无 tz）。

## 9. 真机不可观测行为与钉桩策略（pi-absorb 先例：机制离线钉死、真机栏不声称）

| 不可观测面 | 离线钉桩 | 真机栏（任务 #6，须用户授权，本波**不做不声称**） |
|---|---|---|
| 502/超时恰发生在「服务端已建单、响应未回」窗口 | fake CallApi：seq 自增后抛样本文案 → 断言**发送计数=1、UI 未被二跑、记录带 delivery_uncertain** | SIT 受控写请求配极短 timeout 观察一次；只录 SOP 与现象，不改判据 |
| requests 默认无重试 | §5.17 装配断言 + 源码扫描 | crontab 环境实测一次（顺带 #6 项） |
| 幂等键跨 run 关联真实单据 | 确定性 token 单测（同参等值） | 用真实回收清单人工追一条单（验收动作，非已验证） |

## 10. 未验证/开放问题（C16）

- `classify_risk` 的 save/submit 关键词启发式（agent.py:44）与 C12 判据**无关**——重试门只认 `read_only`；评审时不得把两者混成「双判据」误读（agent 分级是风险申报，delivery 判据是副作用观测）。
- 写任务谎标 `read_only=True` 的元数据审计工具化（review-code 新规则族 DELIVERY-*）未纳入本波——红线 #12，留待有真实误标案例。
- `honors_idempotency=True` 的脚本侧去重兑现质量归业务插件自证，Atlas 只钉「键传递形态」；真实兑现验证随 `atlas schedule` 写档场景（当前无写档 flow 在用，如实留白）。

---

## 评审勘误注（p2_critique · 2026-11-06 · 约束性；详见 design-review-p2.md，冲突以评审为准）

- **C12-A（阻塞级，修复前本设计不得动工）**：本设计立为「要堵的真口子」的形态（tasks.py:186 demo_api 发出后 raise）在自身机制下**不被拦截**——SelectCustomerTask 磁盘 `read_only=False`（tasks.py:243-247）+ 传输走 `actor.demo_api`（CallApi 在场而 seq 不动）→ 判据误读为「未发出」→ 写降级照跑。§2 真值表行 3 的依据改为「CallApi 通用 POST 面可证未发出」；`_perform_via_api` 自定义 `_perform_api` 腿（engine.py:426-427 现成分支判据）对写任务按 attempted=True fail-closed；修复取 (a) 显式能力声明放行读语义 API 腿（推荐，tasks.py 随入 W5 归属表）或 (b) 接受 select_customer 降级被拦并如实声明（须 lead 背书）。§5.9「demo_api 形态=无 CallApi 能力」为误标，两形态各配各测。
- **C12-B**：行号重标：_perform 捕获块=engine.py:401-422（except :411）；heal 腿 :278；healed 更正 :306-309；「:271 supports_api」→:406-408。**§3.2 伪码须保留磁盘 :404-405 `Channel.API and not supports_generic_route` 直返守卫**（伪码现漏，照抄即翻错既有语义）。
- **C12-C**：盘上重试源已**三处**——heal / **步骤级 flaky 重试（:236-275，写 `retried` 键；本设计完全未评估此腿）** / 有界降级。协调补丁 1 的注释扩写与行为测按三源钉。另上报存量 dead-read：`export_runlog`（reporting.py:143）读 `step["retries"]` 永无生产方→flaky 重试导出恒 0——非 W5 本体，列新缺口独立处置。
- **C12-D**：§3.4「同 run 任何自动再触发被配额拦」不实（默认 quota=3 放第二格）；改为「现引擎无自动二次 supply 通路（配测钉）+ quota 满才 EnvBlocked」，§5.14 测设 `create_quota=1`。
- **C12-E**：新增耦合防线测：`DeliveryUncertain` 归类 INTERACTION 使 `waits.is_recoverable`=True，现仅被 `_retry_eligible` 的 read_only 前置偶然挡住——判据级显式拒绝该类型 + step_retry>0 时写任务 perform 计数=1 测钉。
- **C12-F**：token 的 canonical_json 与 creators #61「不可序列化 where 点名」撞序：canonical 失败让位（idem 留空、原报错口径不变），配测钉归属。
- 基线重置：本设计「1448」作废，**磁盘实测 1603 passed**；§6 兼容表复跑数以 design-plan-p2 §5（评审后口径）为准。§4/§5.16/§5.17 净化刀、无隐式重试等磁盘断言经核**成立**（`_HEX_RUN≥32`、src 零 Retry/HTTPAdapter、`atlas_idem`/`idem` 不命中凭据词根）。

---

## 实施收口注（impl_c12 · 2026-11-06 · 施工实况；与正文冲突处以本节为准）

- **C12-A 按 lead 拍板选项 (a) 落地**：声明位取名 `api_leg_side_effect_free`（Task 类属性，默认缺省=未声明）；engine `_perform` 的 attempted 三分支=①自定义腿+已声明→False（放行）②自定义腿+未声明→True fail-closed（**不看 seq**，demo_api 与「腿内真用了 CallApi」两种形态同拦）③通用 POST 腿→`request_seq` 变动判定，`seq_before is None`（CallApi 缺席）→True。`SelectCustomerTask`（tasks.py，W5 归属表内）声明 True 并写明理由；`ReadPtTask` 靠元数据 `read_only=True` 短路，未加声明位（红线 #12 不给零使用加面）。§5.9 两形态各配各测已落。
- **C12-B**：`Channel.API and not supports_generic_route` 直返守卫原样保留并配形测（该形态永不产 DeliveryUncertain）。
- **C12-C**：三源行为测已在 `tests/test_delivery_retry.py::test_three_retry_sources_distinct_keys` 钉死（heal→`healed` / flaky→`retried` / 有界降级→`degraded_to_ui`，三键互不冒充）；reporting.py 注释重写按分区拍板归 impl_c9 落。dead-read（`export_runlog` 读 `step["retries"]`）按 lead 口径**未动**，作为独立缺口留档。
- **C12-D**：措辞已按评审改正（supply 注释与 README 段均写「现引擎无自动二次 supply 通路 + 配额满才 EnvBlocked」）；测设 `create_quota=1` 双触发 + `dsl.provision` 单调用面源码计数钉。
- **C12-E**：**行为未改造**（lead 拍板非本波）——`_retry_eligible` 保持 read_only 前置；本波落的是回归钉：`is_recoverable(DeliveryUncertain)=True` 前提断言 + `step_retry>0 × 写任务 → api_calls==1/ui_called False` + `_retry_eligible(写, DU)=False`。判据级类型拒如后续上，改的即是这两条测的既有口径。
- **C12-F**：让位实现=supply/`ScriptedCreator._with_idem`/`make_scripted_creator` 三处 `except ValueError: idem=""`，#61 键路径点名口径逐字不变；两条测分别钉 supply 面与 creators 面。
- **实现期小差异（如实报）**：`ScriptedCreator`/`make_scripted_creator` 增末位可选参 `idem_entity: str = ""`——设计未给 creator 面 entity 通路，而台账 token 含 entity，不接线就会「同一单两个把手」；缺省空串保持确定性、显式接线可与台账等值。`scope` 恒空（Atlas 不猜作用域）。幂等注入唯一落点在 `_with_idem`（stdin 侧单点，creators 不复制判据）。
- **门禁复跑实测（W5 收口口径，均为 `--collect-only`/全量实跑数，不引第三方口头数）**：W5 归因面 = `tests/test_delivery_retry.py` **48 项** + `tests/test_privacy_drift.py` 新增 **1 项**；三文件合跑（delivery + waiting + privacy）= **163 passed**；全量 `PYTHONPATH=src python -m pytest -q` 收口时点 = **1777 passed / 0 failed**（与 impl_c9 并行波叠加长，终态计数三处归 lead 统一收口，我不改数）；`compileall` OK、`contracts --validate` rc0、`doctor` rc0、X 哨兵 158 passed。存量 healing #108 组、`test_plugin_and_legacy.py` 降级/OSError 红线组零翻红；`_perform` attempted 三分支与 `record["delivery_uncertain"]` 补键经 impl_c9 独立复核语义未变、与其 AwaitingHuman elif 互不重叠。收口后期加固一处可信度（§5.17 的源码扫描测）：原只断言空集，现钉**四层**——①合成正样本自证判据串本身有效（tmp 写含 `HTTPAdapter(`/`Retry(`/`max_retries =` 的文件，同一判据必须三个全认出，且对 tmp 重跑扫描命中恰为该文件）②`len(scanned) > 50` 面非空 ③`{abilities,engine,probes,creators} ⊆ 扫描面`（按本测自己的依赖面定，不抄别测清单）④全域 `hits == []`。本测盘上**无合法命中可锚定**（正确结果就是空），故用合成正样本代替 impl_c9 的「已知合法命中 ∈ hits」式第三层；两种载体的失效方向都是**测先红、代码后查**，不留 `== []` 冒充防线。加固后测数不变（48 项），复跑全量 1777 passed / 0 failed。PRIVACY/`test_privacy_drift` 已按红线 #8 补两键；CLAUDE.md `delivery.py` 行与 AGENTS.md §3 不变量条已落盘（文档并发窗由 impl_c9 代插后逐字核对，各 1 处无重复）。真机栏（§9）未做未声称。
- **§9 真机栏范围勘误（real_ops 于 #6 起跑时抓到，是我的口径错，不是他的范围窄）**：本表第 2 行原写「crontab 环境实测一次（**顺带 #6 项**）」——#6 的授权面是 lead 定的五项（schtasks 引号 / Ctrl-C 竞态 / cron env 继承链 / DST fold / C3 legacy 回归），**不含**任何需要发真实请求的 requests 重试实测；我把它写成「顺带」等于自作主张替 #6 扩了授权，会让终验误以为该项已在真机确认过。**正确口径**：三项真机栏（①502 投递不确定窗口②requests 无隐式重试的真机复测③idem 真实追单）全部**待用户另行授权**，其中①③还要烧真实写会话；②的判据面已由离线四层自证钉死，真机复测只是增强证据、不是缺口。§9 表格本身按一期惯例不重写主体，以本条为准。

## §10 后续波次闭合（2026-11-06，lead 放行「都做」波）

### C12-E 判据级拒绝：**已落地**（此前是「只测不改行为」）
`failures.NEVER_AUTO_RETRY = (AwaitingHuman, DeliveryUncertain)` 单点，`waits.is_recoverable`
在**任何文本判据之前**按类型拒（唯一消费方，引擎不抄第二份名单 #92）。升级的理由不是「更严格」，
而是旧防线本身站不住：`_retry_eligible` 靠 `task.metadata.read_only` 挡 DU，而 C12-A 已经论证过
「自定义 API 腿不经 CallApi，seq 不动 ≠ 未发出」——**连副作用是否存在都要 fail-closed 猜的东西，
不能反过来当「可以放心重跑」的放行依据**。业务插件对一个声明 `read_only=True` 的 Task 抛
`DeliveryUncertain`，旧形态就会穿过元数据门把写重跑一遍。

顺带堵掉同族的第二个案发点：`is_recoverable` 里 `_is_transient` 求值**早于** `classify_failure`，
所以 reason/cause 文案带 "navigation"/"detached" 的 `AwaitingHuman` 也会被判可重试（引擎的分支
次序只挡住了外层那一次，`waits.retry(retry_on=...)` 循环内二次抛出仍漏）。类型闸置前后，
`test_never_auto_retry_beats_transient_text` 用「同文案的普通 RuntimeError 必须仍判可重试」自证
非恒真。**归因面不变**：DU 仍 classify→INTERACTION、仍保持 failed（红线 #7），钉在
`test_delivery_uncertain_never_step_retried_even_with_retry_enabled` 里两条同时成立。

### 真机栏三项：**已闭合，但闭合方式与原文不同——不打共享 SIT**
原文把①③写成「需烧真实写会话」。实做时先否掉了这个取证路径：**在共享 SIT 上制造 502/半开连接
等于对共用环境施压，那是攻击面不是取证**；而 ③ 若真打两次 SIT 建单，验的是「ICS 后端认不认
我们自造的 `atlas_idem` 键」——设计从未主张过这件事（§3.5 原文「引擎只传递键，脚本侧去重兑现归
业务」），花真实写会话去确认一个已知的否定答案、还在测试环境留下重复单据，收益为负。
改用**本地 sink（127.0.0.1 真监听端口）+ 真 requests + 真 socket + 真 subprocess**，覆盖的正是
要验那一层：「请求真的离开了进程、对端真的没给出可判定响应」。实跑数（2026-11-06）：

| 项 | 载荷 | 实测 |
|---|---|---|
| ② requests 无隐式重试 | 真连接 hang / 真 502 / 真 408 各一发 | 服务端 accept **=1 / =1 / =1**，客户端依次 `ConnectionError(RemoteDisconnected)`、`ConnectionError(ConnectionResetError 10054)`、同上。**成立** |
| ① 投递不确定窗口 | 自定义 API 腿真发出后按业务插件惯例抛 RuntimeError（对端真 502） | accept=1、`request_seq`=1、`_perform_api`=1、**UI 降级=0**、抛 `DeliveryUncertain`（文案含「无法证明未发出」）→ 拦降级且不重发。**成立** |
| ③ 幂等把手可靠性 | 真 subprocess 建单脚本，honors_idempotency=True 两次同 where | token `6c11963e291bb9ea` 两次同值；第二次 `{orderNo: SP000001, deduped: true}` **未二次建单**；换主数据→换 token（`20274f191363537e`）；**对照**：honors=False 时不注入键→第二次建出 SP000002、台账 1→2（证明去重依据确实是那个键，不是脚本碰巧）；len=16 < 32hex 避开净化刀。**成立** |

### **新发现（不在原登记清单，取证时撞到）：真·断连不走 DU 标记**
`E3a`：通用 POST 腿撞真连接被掐 → 引擎降级腿捕获的是 `(NotImplementedError, RuntimeError)`，
而 socket 层炸的是 `requests.ConnectionError`（继承 OSError），**不在捕获内** ⇒ 异常原样传播，
`delivery_uncertain` 键不写、终态按一般失败归因。实测 accept=1、seq=1 ⇒ **不双发，红线安全**，
但台账看不出「这一步其实需要人工核实数据」——而断连恰恰是投递不确定最典型的形态。
未在本波顺手改（新增捕获项会连带改变**只读** Task 的行为：断连从「直接失败」变成「降级 UI 再跑
一次」，那是另一次真实网络往返，属独立口径决策）。登记为待拍板项：若要收口，正确形态应是
「写任务断连→补 DU 标记，读任务维持原传播」，判据仍归 `delivery.safe_to_auto_reexecute` 单点。
