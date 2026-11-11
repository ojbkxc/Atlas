# Atlas 接口测试常见问题判读手册

> 来源：ICS 接口自动化/JMeter 巡检/Atlas 施工中实锤的问题形态。每条给出**症状 → 根因 → Atlas 现有处置位置**。
> 用途：新同事判读失败现场、新波次扩展判据时先查这里，避免重复踩坑。

## 1. 认证与会话

### 1.1 接口返回 HTML = 认证过期
- **症状**：接口 HTTP 200、`ok=True`，但 body 是整页 HTML（登录页），下游拿 HTML 当 JSON 解析，烂到远处才炸。
- **根因**：ICS 会话失效后网关不回 401，而是**重定向/回渲染登录页**——状态码与 Content-Type 都不可靠（异常网关/mock 服务器回登录页常不带 `text/html` 头，或大小写畸形）。
- **Atlas 处置**：`abilities.py:147-153`（#122 面B）——判据收敛「头**或**正文像 HTML」两形态都拦；正文判据只在 JSON 解析失败落 `raw` 时求值，命中后返回 `ApiResponse(ok=False, data={"auth_expired": True, "message": "会话失效或被重定向到登录页"})`。**合法 JSON API 不存在返回整页 HTML 头的形态**，此判据不误伤。
- **分类**：`failures.py:90` SESSION 词表含 `auth_expired`；`api_evidence`（failures.py:227）见 `data.auth_expired` 即归 SESSION。

### 1.2 裸 401 + JSON 应答（ICS 过期会话最常见形态）
- **症状**：401，body 是网关 JSON（如 `{"error":"unauthorized"}`），无任何 HTML 痕迹。
- **Atlas 处置**：`abilities.py` 会话保活以 **401 状态码为权威判据**自动触发重烘焙（#141 面C）——403/500/200 不误触发（403 可能是权限而非会话，500 可能是业务异常，乱重登会烧登录配额）。
- **分类**：`api_evidence` 401/403 → SESSION。

### 1.3 会话串环境（UAT 会话打 SIT）
- **症状**：反复 401/登录页，但 Cookie 明明刚烘焙过。
- **根因**：会话文件按环境独立——UAT 会话打 SIT 必 auth_expired（WO 下推模块实测，memory 在册）。
- **判读**：先查 `--account/--ou` 与会话文件是否对环境，再怀疑过期。

## 2. 请求体与报文形态

### 2.1 form 串走 `json=` 得假 400
- **症状**：POST 返回 400，报文看起来「没问题」。
- **根因**：JMeter 非 raw 多参数迁移产物是 form 串（`custNo=1&page=2`），一律 `json=` 会被 requests 包成**带引号的 JSON 字符串字面量**发出，后端收脏数据报 400——**不是接口坏了，是发送语义错了**。
- **Atlas 处置**：`abilities.py:126-132`——body 按 requests 语义分流：`dict/list→json=`、`str/bytes→data=` 原样、其余类型发送前点名拒绝（不让 requests 内部裸崩冒充框架缺陷）。

### 2.2 报文腰斩 / bool 当 body
- **症状**：巡检发残缺报文得假 400。
- **根因**：旧实现 `[:500]` 腰斩大报文、`bool` 当 body 当场崩。
- **纪律**：报文必须来自真实捕获（`scripts/capture_save_request.py`），不得凭字段名猜（AGENTS §1 条 16）。

## 3. 状态码分类口径（api_evidence 判据表）

`failures.py:218-235` UI 失败重放同接口的归因次序：

| 重放结果 | 归因 | 理由 |
|---|---|---|
| ok=True | INTERACTION | API 正常=UI 操作问题 |
| 401/403 或 `data.auth_expired` | SESSION | 会话失效 |
| status ≥ 500 | ENV_BLOCKED | 网关/服务端环境问题（5xx 不是测试代码能修的） |
| data 含契约词（bug-/required/invalid…） | CONTRACT | `_RULES` 词表 |
| data 含「查询无结果」类（`_DATA_ABSENT`） | DATA | 主数据缺失，换数据不修代码 |
| 其余 | ENV_BLOCKED | 兜底归环境，不冒充待诊断缺陷 |
- **状态码归一化陷阱**：HAR/JMX 录制常把 status 存字符串 `"500"`——`"500">=500` TypeError 裸崩、`"401" in {401,403}` 恒假漏判。`_status_code`（failures.py:204）数字串转 int、非数值归 None 走文本回落。
- **data 形态零假设**：ICS 列表接口 data 常为数组、网关错误常为字符串、失败常为 null——对非 dict 直接 `.get` 让定性器先死（failures.py:223-226）；data=null 时定性文本回落 error，不被 `"None"` 挤掉真实根因。

## 4. 创建/保存类操作的状态码错误类型（ICS 实锤）

| 形态 | 症状 | 定性 |
|---|---|---|
| 税率 NPE | 保存时 500，报文含 `NullPointerException`、单号 SPM-653 | **已知产品缺陷**（failures.py:83 词表 `bug-653`），归 ENV_BLOCKED，非脚本问题 |
| 重复提交 | 单号唯一约束报错 | 业务幂等拦截，非缺陷；创建类 Task 必须走幂等台账（`creators.py`）+ C12 幂等把手 `atlas_idem` |
| 字典值消失 | 下拉/校验报「不在枚举」 | **主数据漂移**（PT 字典 90DI→120DF 实测），归 DATA；等回补，不绕测试代码 |
| 价格缺失联锁 | 建单 skip 放大到后续 8 条 | 主数据缺失（SPMM-150），归 DATA；c3-baseline §9 归因表 |
| 生产写护栏 | `生产环境禁止` | 环境策略阻挡非缺陷（failures.py:88 ENV_BLOCKED） |
| 假 400 | form 串/腰斩报文（§2） | 发送语义错，修报文不修接口 |

## 5. 判读纪律（负面教训）

1. **别信单一信号**：状态码、Content-Type、正文三处都可能撒谎（1.1 三形态实证）。
2. **定性器不能先于被定性对象死**：对 data/status 形态零假设，坏形态回落不裸抛（failures.py 全文纪律）。
3. **重试不掩盖定性**：确定性数据问题（字典消失）不适用抖动重试，如实记 failed（c3-baseline §9 TC13 先例）。
4. **净化了再定性=损归因**：`?code=401` 关键词只在敏感值里的样本，先净化会把 SESSION 翻成 unknown（failures.py:110-113 C4②）。
5. **新形态扩展入口**：症状可判读→先看 `_RULES`/`api_evidence` 是否已覆盖；需新增词表/判据时走对抗评审，勿无声加规则（洗绿入口红线）。
