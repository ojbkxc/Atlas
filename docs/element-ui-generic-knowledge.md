# Element UI 通用知识包 —— 分类台账

> 来源：`src/atlas/plugins/snalai/knowledge/`（2026-11 对某国内 SaaS 商城后台两端的功能测试，
> admin 管理后台 + seller 商家后台）。本台账逐条记录**每个原始条目**的通用性判定与去向，
> 作为 `src/atlas/plugins/_common/knowledge/` 三个通用包的提炼依据。
>
> 判定口径：
> - **通用**：整条脱离平台坐标后可直接复用的机制判据/方法 → 抽入 `_common/knowledge/`。
> - **平台特有**：内容是平台事实/业务坐标/具体缺陷 → 留在 `snalai` 平台包，不进通用包。
> - **拆分**：一条里混着「通用方法」与「平台事实」→ 方法抽入通用包，平台部分留在 snalai。
> - 无法判定是否通用的一律**留平台包**，并在 §4 记明理由（不写「看起来对」的猜测）。

通用包产物：`_common/knowledge/components.json`（15 组件，login 增 `login_page_security` 模型键，见 §7）/
`pitfalls.json`（47 + wave2 35 = **82 条**，`scope` 只用机制标签、`status` 一律 `pending_review`，见 §7）/
`domains.json`（新平台接入检查清单，wave2 增 `stack_entry_and_accounts` 节，见 §7）。
**通用包已扫描确认不含**：平台名、真实域名、真实页面路径、账号手机号、客户业务字段名。

---

## 1. components.json（16 条原始条目）

| 原条目 id | 判定 | 去向 | 理由 |
|---|---|---|---|
| `login` | 拆分 | 通用 `login` + 留 snalai | 通用部分：`input.el-input__inner` 选择器、登录成功判据（业务只读接口 200 而非 URL）、验证码 OCR/state 复用/凭据纪律方法。平台部分留 snalai：真实 `/login` 路径、自定义 class `img.verification-code`、按钮文案「确认登录」、`admin_access_token`/`admin_refresh_token` 等 localStorage 键、`SNA_*` env 名 |
| `dialog_visibility` | 通用 | 通用 `dialog_visibility` | 纯 Element UI 弹层可见性机制（`.el-dialog__wrapper` 多副本、取最后一个可见、标题+label 断言），无平台坐标 |
| `add_button` | 通用 | 通用 `add_button` | 五态分类（dialog/route/newtab/toast/nothing）是通用机制；去掉示例路由 `/shop-guide/add-guide-goal` |
| `advanced_search` | 通用 | 通用 `advanced_search` | popover 面板挂 body、隐藏/可见同名按钮、`:visible` 过滤、「清空」二次确认——通用 Element UI 铁律 |
| `row_action_dropdown` | 通用 | 通用 `row_action_dropdown` | 固定右列 + dropdown 挂 body + 行索引算固定列重复，通用；去掉业务菜单项示例「确认入库」 |
| `export_button` | 通用 | 通用 `export_button` | `el-dropdown-selfdefine` 触发器 ≠ 动作按钮，通用；去掉业务按钮文案 |
| `table` | 通用 | 通用 `table` | 幻影重复行 + 「断言前对照接口 total」+ 「换入口先 dump DOM」通用；去掉「商品列表」业务名 |
| `seller_order_table` | 平台特有 | 留 snalai | `table.my-table` / `tr.bg-order` 是平台**自定义 class**，非 Element UI 通用选择器；通用形态已由 `table` 条「换入口先 dump DOM」覆盖 |
| `pagination` | 通用 | 通用 `pagination` | `.el-pagination li.active`（不是 is-active）、翻页断言方法，通用 |
| `date_range` | 通用 | 通用 `date_range` | date picker 面板点 `td.available`、不能 `fill()`，通用 |
| `checkbox` | 通用 | 通用 `checkbox` | 点 `.el-checkbox__inner`、`.el-checkbox__original` 隐藏，通用 |
| `dialog` | 通用 | 通用 `dialog` | `.el-dialog__footer button` + 按钮文案空格 + 点完验副作用，通用 |
| `select_dropdown` | 通用 | 通用 `select_dropdown` | 下拉选项 append body + 隐藏副本过滤，通用 |
| `nav_menu` | 通用 | 通用 `nav_menu` | 导航项文本污染按文本抓按钮 → 限定内容区容器，通用；去掉业务层级文案 |
| `rich_text` | 通用（库级） | 通用 `rich_text` + 结论留 snalai | UEditor 是第三方库、`.edui-editor` 是库 class，跨站可复用；「本项目无打印功能」是平台结论 → 留 snalai（另见 `snl-035`） |
| `session_scope` | 通用 | 通用（并入 pitfalls `gen-027`） | 「任何 403 触发全局登出、误杀并发会话」是通用前端机制坑，无选择器，归为 pitfalls |

小计：通用 13 条（含 login 拆分出的通用部分）+ 拆分 1 条（login）+ 平台特有 1 条（seller_order_table）+ session_scope 转为通用 pitfall。

---

## 2. pitfalls.json（48 条原始条目 snl-001 ~ snl-048）

| 原 id | 判定 | 去向 | 理由 |
|---|---|---|---|
| snl-001 | 通用 | `gen-001` | popover 面板 `.first()` 命中隐藏副本 → `:visible` 过滤，通用；去平台（t-finance 收款单） |
| snl-002 | 拆分 | `gen-040` + 留 snalai | 机制「清空是二次确认触发器、清空≠列表刷新」通用；「清空后列表不自动刷新=真缺陷」是平台业务行为留 snalai |
| snl-003 | 通用 | `gen-016` | evaluate 改 value 不触发 v-model（vue2），通用；去平台 |
| snl-004 | 通用 | `gen-002` | `el-dropdown-selfdefine` 触发器 ≠ 动作按钮，通用 |
| snl-005 | 通用 | `gen-012` | 建单页路由守卫要求从列表进入，通用 SPA 机制；去具体路径 |
| snl-006 | 通用 | `gen-013` | 详情页深链受阻于内存态，通用；去具体路径 |
| snl-007 | 通用 | `gen-015` | 校验可能走 toast；判据=是否真的发出写请求，通用 |
| snl-008 | 通用 | `gen-004` | 按钮文案空格 → 正则，通用 |
| snl-009 | 通用 | `gen-005` | checkbox 点 inner，通用 |
| snl-010 | 通用 | `gen-006` | 日期区间点 picker 面板，通用 |
| snl-011 | 通用 | `gen-010` | 导航文本污染按钮抓取，通用；去业务层级文案 |
| snl-012 | 拆分 | `gen-009` + 留 snalai | 方法「断言前对照接口 total 判断渲染层重复」通用；「商品列表 ×3、具体行数」是平台数据留 snalai |
| snl-013 | 通用 | `gen-008` | 固定列重复渲染致行错位，通用 |
| snl-014 | 通用 | `gen-017` | 写接口 params vs JSON body 逐接口确认，通用；去具体端点 |
| snl-015 | 通用 | `gen-018` | 业务校验用 500 承载 → 按 message 分类，通用 |
| snl-016 | 通用 | `gen-019` | 类型不符 → 框架异常外泄内部包名（信息泄露）；**剥离 `com.enation.app.javashop.*` 平台包名**后通用 |
| snl-017 | 拆分 | `gen-026` + 留 snalai | 方法「认证头格式必须实测、不要套 Bearer」通用；「裸 JWT / `admin_access_token`」平台事实留 snalai |
| snl-018 | 通用 | `gen-027` | 403 触发全局登出误杀并发会话，通用 |
| snl-019 | 通用 | `gen-030` | 导出端点可能返回 JSON 非 xlsx + filename 硬编码，通用 |
| snl-020 | 通用 | `gen-020` | 分页参数无上限校验（超大/负数），通用 |
| snl-021 | 通用 | `gen-022` | 部分端点忽略时间筛选参数 → 按端点逐个验别外推，通用 |
| snl-022 | 通用 | `gen-021` | LIKE 通配符未转义 → 全表（≠注入），通用 |
| snl-023 | 通用 | `gen-031` | 导出可能为全量（page_size 不生效），通用 |
| snl-024 | 通用 | `gen-032` | 状态机无幂等/终态可回/草稿直达终态，通用检查清单 |
| snl-025 | 通用 | `gen-023` | 数值字段负数/超大被接受（200+GET 复查），通用；去具体业务字段 |
| snl-026 | 拆分 | `gen-041` + 留 snalai | 方法「对账三式」通用；「出库成本符号反向」是平台业务缺陷留 snalai |
| snl-027 | 拆分 | `gen-041` + 留 snalai | 方法同上；「库存三套账不一致」平台缺陷留 snalai |
| snl-028 | 拆分 | `gen-041` + 留 snalai | 方法「公式自洽对账」通用；「毛利率公式算错」平台缺陷留 snalai |
| snl-029 | 拆分 | `gen-024` + 留 snalai | 机制「同接口字段校验不对称值得专门扫」通用；「need_point 负数致等级删不掉」平台留 snalai |
| snl-030 | 拆分 | `gen-042` + 留 snalai | 机制「同 popover 累加条件不清空致误判、复测重开页面」通用；「constitution=NONE 枚举」平台留 snalai |
| snl-031 | 通用 | `gen-035` | 无冲销入口致写操作不可逆、先摸清可删/可置终态，通用 |
| snl-032 | 通用 | `gen-029` | 越权矩阵必须用受限账号、写接口只测「是否被拒」，通用 |
| snl-033 | 拆分 | `gen-045` + 留 snalai | 方法「先区分凭证缺失与后端缺陷、依赖外部系统先排除」通用；「SCRM 企微 e=60020」平台留 snalai |
| snl-034 | 拆分 | `gen-025` + 留 snalai | 方法「僵尸端点 → 先找 bundle 调用点」通用；「退款审核僵尸端点」平台留 snalai |
| snl-035 | 拆分 | `gen-014` + 留 snalai | 机制「print 来自富文本编辑器插件」通用；「本系统无打印功能」是平台能力边界结论留 snalai |
| snl-036 | 拆分 | `gen-046` + 留 snalai | 方法「搜索触发方式多样、可能靠 Enter，别急着判无搜索」通用；「seller 列表页普遍无查询按钮」平台事实留 snalai |
| snl-037 | 拆分 | `gen-046`（并入「先 dump DOM」）+ 留 snalai | 方法通用；`tr.bg-order`/`table.my-table` 平台自定义选择器留 snalai |
| snl-038 | 通用 | `gen-028` | 认证≠授权、前端三层全绿≠权限生效、必须做接口重放第四层，通用 |
| snl-039 | 拆分 | `gen-023`（方法已在）+ 留 snalai | 方法「数值字段篡改」已由 gen-023 覆盖；「入库单数量零校验直接置终态」平台缺陷留 snalai |
| snl-040 | 拆分 | `gen-047` + 留 snalai | 方法「dump 所有 table 排除隐藏测量行 + 查空态」通用；「盘点单选品功能死路」平台缺陷留 snalai |
| snl-041 | 通用 | `gen-033` | 状态机用「终态 × 操作入口」矩阵逐格验，通用 |
| snl-042 | 通用 | `gen-034` | 幂等测试用接口层连发同一请求，通用 |
| snl-043 | 平台特有 | 留 snalai | 退款是否改状态取决于售后类型、收款单不冲销——属业务财务口径，非通用机制（见 §4） |
| snl-044 | 通用 | `gen-036` | 删除/清空类按钮可能无二次确认、首次点击在自建数据，通用；去平台事故细节 |
| snl-045 | 通用 | `gen-038` | 前端把 500 吞成空态、打开页面同时抓 responses，通用 |
| snl-046 | 通用 | `gen-039` | 断言默认视图必须抓请求实参，通用 |
| snl-047 | 平台特有 | 留 snalai | 收银台现金支付断链是平台前端缺陷（见 §4）；其方法「点击后看 network」已由 `gen-037`/`gen-015` 覆盖 |
| snl-048 | 通用 | `gen-020`（并入） | 分页参数无校验、total 与实际条数自相矛盾，与 snl-020 同族合并 |

---

## 3. 数量小结

| 类别 | components | pitfalls | 合计 |
|---|---|---|---|
| 通用（整条抽出） | 13 | 31 | 44 |
| 拆分（方法抽出 + 平台部分留下） | 1 | 16 | 17 |
| 平台特有（整条留 snalai） | 1 | 1 | 2 |
| 转为其它形态（session_scope→pitfall） | 1 | — | 1 |
| **原始条目合计** | **16** | **48** | **64** |

pitfalls 通用抽出 31 条（整条）+ 16 条拆分中的方法段，最终去重合并为 `_common/knowledge/pitfalls.json` 的 **47 条**（`snl-020`+`snl-048`、`snl-026/027/028` 三合一等做了合并）。

---

## 4. 无法判定 / 有意的取舍（留平台包，写明理由）

| 条目 | 取舍 | 理由 |
|---|---|---|
| `snl-043`（退款链路口径） | 留 snalai | 「退款是否改订单状态取决于售后类型」「收款单不冲销退款」是**业务财务语义**，换平台未必成立；「按类型分桶、不要合并统计」若泛化会丢失具体分桶口径，不写「看起来对」的猜测 → 不抽 |
| `snl-047`（收银台现金支付） | 留 snalai | 「点确定不发请求、按钮恒禁用」是平台**具体前端缺陷**；其可复用方法（点击后看 network 是否有请求）已由 `gen-037`/`gen-015` 覆盖，故本条不另抽 |
| `components.seller_order_table` | 留 snalai | `table.my-table`/`tr.bg-order` 是平台自定义 class，非 Element UI 通用；抽出来会成为「看起来通用其实绑平台」的伪通用选择器（红线 #12） |
| `components.login` 的平台段 | 留 snalai | 真实路径 `/login`、自定义 class `img.verification-code`、token 的 localStorage 键名、`SNA_*` env 名，均为平台坐标 |

**通用包冻结边界**：`rich_text` 的 `.edui-editor` 选择器按「第三方库（UEditor）库级 class」判为可复用——
若未来确认目标平台不用 UEditor，此条应随平台插件覆盖，不影响其余通用条。

---

## 5. scope 机制标签词表（pitfalls.json）

按机制标签标注（**不含任何平台名**）：`element-ui` / `vue2` / `api` / `state-machine` / `authz` / `export` /
`delegation` / `data-hygiene` / `cleanup`（任务约定 9 个）+ 扩展机制标签 `routing` / `validation` / `query` /
`idempotency` / `session` / `infoleak` / `search`（9 个约定标签无法覆盖的机制面，均为通用机制、非平台名）。

### 5.1 `generic` 可见性标记（team-lead 裁定）

通用包 pitfalls.json 的**每一行** scope 列表都追加 `generic`：通用条目 = 通用包内所有行都带 `generic`。
引擎可见性判据改为 **`domain in scope or "generic" in scope`** —— 没有 `generic`，通用条目对任何平台域
（包括 snalai）都不可见。对照：

- **通用包行**：`scope` = 机制标签（…/search）+ `generic`，id 前缀 `gen-*`。
- **平台行**（各插件 `knowledge/pitfalls.json`）：`scope` = 平台名（如 `snalai`）+ 机制标签，**不带 `generic`**；
  id 前缀 `snl-*`。

**id 前缀不冲突**：`gen-*`（通用包）vs `snl-*`（平台包），两者不会在同一域查询里被误判为对方。
组件（components）与 domains 不经 domain 过滤——components 按组件名合并、domains 按 key 合并，**不需要** `generic` 标记。

---

## 6. 自检

```bash
py -3.11 -c "import json; [json.load(open(f,encoding='utf-8')) for f in ['src/atlas/plugins/_common/knowledge/components.json','src/atlas/plugins/_common/knowledge/pitfalls.json','src/atlas/plugins/_common/knowledge/domains.json']]; print('JSON OK')"
grep -rniE "<平台标识词表>" src/atlas/plugins/_common/knowledge/   # 应无输出（词表=平台名/业务标识/真实路径段，本地维护，不进仓库）
```

---

## 7. 第二轮提炼（wave2，2026-11-14）

> 输入：三份侦察提案（仓库外 `C:/tmp/`：`gw2_proposal_api.json` 20 条 / `gw2_proposal_docs.json` 14 条 /
> `gw2_proposal_platform.json` 12 条，共 46 条）。提案里的原始锚点（端点 JSON pointer 等平台坐标）**不进仓库**；
> 本节与条目 source 字段只用「提案编号 + 机制名 + 业务面中性描述」溯源。合并落地唯一写手由 team-lead 指定，
> 现有 47 条 gen-001~047 零改动（保守原则：既有条目没说透的面一律用新条目承接，不动原文）。

### 7.1 产出

| 文件 | 变化 |
|---|---|
| `pitfalls.json` | 47 → **82 条**：新增 `gen-048` ~ `gen-082`（35 条）。schema 与现有条目完全一致（id/trigger/symptom/root_cause/remedy/scope/severity/source/status），status 一律 `pending_review`，scope 全部为 §5 词表内标签 + `generic`（词表零扩展） |
| `components.json` | `login.models` 新增 `login_page_security` 键（docs 提案 #14：登录页安全面顺手检查三点），组件数不变（15） |
| `domains.json` | 新增 checklist 节 `stack_entry_and_accounts`（docs 提案 #12：技术栈识别 / 入口拓扑 / 权限准备），其余六节零改动 |

### 7.2 三方归并决策（46 条提案 → 35 条 pitfalls + 2 条归位 + 0 条丢弃）

同一机制出现在多份提案时归并为一条、来源合并，共 10 组（其中 platform #5「关系型篡改探针」按其三个探针拆三分）：

| 新 id | 归并的提案 | 备注 |
|---|---|---|
| gen-048 | api #1 + platform #5② | UI 排除输入差集；「来源=目标」探针并入 |
| gen-052 | api #5 + platform #8 | 静默改写/钳制/重算三态判定；severity 取并集高值 |
| gen-053 | api #6 + platform #12 | 条件触发型故障对照取证 |
| gen-055 | api #8 + docs #2 + platform #5① | 跨字段一致性写入探针（头额 vs 明细脱钩），三份提案同一机制 |
| gen-058 | api #11 + platform #11 | 高频轮询污染流量统计（同源端点实测） |
| gen-061 | api #14 + platform #7 | 读回写更新载荷（展示 DTO ≠ 更新 DTO） |
| gen-062 | api #15 + platform #5③ | 引用完整性（幽灵引用/静默 null） |
| gen-065 | api #18 + platform #10 | 长文本注入串落库 + 超长 500 兜底 |
| gen-066 | api #19 + platform #2 | 写入无校验 × 清理有约束的锁死连锁；severity 取并集高值 |
| gen-067 | api #20 + platform #9 | 超发字段（mass-assignment）阴性判读 |

归位（不进 pitfalls）：docs #12 → `domains.json`（checklist 增项，其 remedy 本身已注明落点）；docs #14 → `components.json`（login 层）。

丢弃：**无**。46 条提案全部落地（35 条新 pitfalls 含 9 条归并来源 + 2 条归位）；三份侦察均声明每条对应一次
真实实测（notes.json / api-pitfalls.md / playbook 判据），未发现需按「宁缺毋滥」剔除的推测性条目。

### 7.3 与现有 gen-001~047 的关系（相邻不重复判定）

全部 35 条在 source 字段带一句话相邻判定，摘要：

- **现有条目只说了一半，用新条目承接另一半（不动原文）**：gen-048↔gen-032⑤（「UI 上无入口」vs 输入差集探测法）、gen-055↔gen-041（读侧对账 vs 写侧脱钩探针）、gen-057↔gen-041（「取对字段」一句 vs 数据反推法）、gen-066↔gen-024/gen-035（不对称面/清理盘点 vs 连锁机制）、gen-076↔gen-036（两格 vs 四步梯度）、gen-079↔gen-041（静态恒等式 vs 方向性断言）、gen-082↔gen-026（格式实测 vs 多头组合）、gen-069↔gen-042（两格 vs 第三格「排除自家前序写入」）、gen-075↔gen-043（计数闸门 vs 不合格件处置）、gen-053↔gen-045（凭证排除 vs 参数对照）。
- **特例 → 通用化**：gen-054↔gen-022/gen-031（时间筛选/导出分页两特例 vs 筛选参数通用形态+空值哨兵）。
- **不同类不混**：gen-051↔gen-019（密钥回显=设计面 vs 异常栈外泄=实现面）、gen-060↔gen-027（网关伪装 200 vs 前端全局登出）。
- **全新面**（无相邻）：gen-050、gen-058、gen-059、gen-063、gen-064、gen-068（否定性结论三要素——gen-037 只覆盖正向「没反应」类）、gen-070~074、gen-077、gen-078、gen-080、gen-081。

### 7.4 对 wave1 §4 两处取舍的重新裁定（方法与口径分离）

| wave1 §4 判定 | wave2 裁定 | 理由 |
|---|---|---|
| snl-043 整条留平台包（「按类型分桶、不要合并统计」若泛化会丢失具体分桶口径，不写看起来对的猜测） | 只抽方法 → gen-081 | gen-081 不携带任何分桶语义：断言按子类型分桶做、**桶间差异记录为口径事实而非缺陷**——方法本身不猜口径，wave1 顾虑解除；平台业务口径本体仍留平台包 |
| snl-026「出库成本符号反向」判平台缺陷留平台包（对账三式已抽入 gen-041） | 只抽方向断言方法 → gen-079 | wave1 抽了静态恒等式、未收「变化方向」维；gen-079 只收「逐维断言方向一致」探针方法，平台缺陷本体不动 |

### 7.5 wave2 自检

自检命令同 §6（中立性 grep 词表按任务口径追加本波新增的平台侧标识词，词表本地维护、不进仓库），结果：

- `py -3.11` JSON 解析三文件 OK；82 条 id 连续（gen-001~082）、9 键齐全、trigger 非空、status 全 `pending_review`、scope 均含 `generic`。
- 中立性 grep（§6 词表 + 本波追加的平台标识词）对三个知识文件：**无输出（CLEAN）**。
- 全量 pytest（`PYTHONPATH=src py -3.11 -m pytest -q`，117.5s）：**9 failed / 2535 passed / 8 skipped，与 wave2 前基线逐数一致，零新增失败**（9 个失败全部为既有环境类失败：legacy compat xcheck / migration sentinels，与知识数据无关）。
- 台账本节不含平台坐标；提案原始锚点（含端点 JSON pointer）留在仓库外提案文件，未入库。
