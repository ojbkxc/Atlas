# wave4 A 轴：UI 族可移植面（换平台时的静默失效）

> 本波处理「换平台后照着经验做、结论假绿或假红」的静默失效面。uf-1/uf-2/uf-3 三条全部落地。

## 1. 背景与判据

Atlas 给不同 web 平台做功能测试，经验沉淀在知识库里。但知识里有一批**引擎假设**：
等待判据默认 Element UI 的选择器、读取判据默认 Vue2 运行时。换平台后这些假设
**静默失效**——不报错，但结论不可信。

本波把这三处变成**数据驱动的显式判据**：族 = 知识数据，判据缺席就点名，绝不静默放行。

## 2. 本波做了什么

### uf-1：遮罩缺席时的「未加载」结论（`waits.py`）

`wait_for_mask_gone` / `wait_for_rows` 在遮罩缺席时，旧实现是
`wait_for_selector(state="hidden")` —— 对**不存在的选择器恒满足**，静默秒回「已落定」，
后续读 stale 数据 → 假绿/假红。

现改为：先用族**前缀特征**（DOM 层 class token 以族前缀开头）确认页面确属本族，
特征缺席点名 `UiFamilyMismatch`。

**判据为什么用「前缀 DOM 存在性」而不是固定签名选择器列表**：成员资格判据不能用
固定组件清单——纯展示页/详情页（只有 `.el-card`/`.el-row`/`.el-col`/`.el-statistic`）
是合法 Element UI 页面，却不含 `button/table/menu/dialog/input` 任何一个。终验用真实
Chrome 取证确认了这一点（见 §4）。按 class **token 边界**匹配，不用子串——
`model-x`/`panel-y` 含 "el-" 子串但不是本族 token，子串匹配会假阳=把非本族当成本族，
正是 uf-1 要防的东西。

`signature` 字段退为**诊断/展示**（doctor 与异常文案点名用），不作运行时判据。
族表 `note` 写清这个分工。

### uf-2：运行时错配点名（`waits.py`）

`wait_for_rows` / `wait_for_vue_field` 的 JS 用哨兵字符串分流：

- 表格在却无 `__vue__`、`vm.data` 非数组 → 点名 `RuntimeError`（旧实现 `return 0`，
  把运行时/族错配伪装成「未达 N 行」）；
- `#app` 缺席 → 按未就绪重试（导航期常态）；`#app` 在却无 `__vue__` → 点名。

合法空表（`data: []`）路径不变。

### uf-3：族声明与知识卫生（`knowledge.py` / `_common/knowledge/components.json`）

- 通用包 `components.json` 顶层声明 `"family": "element-ui"`；
- `login.submit_generic` 去掉裸 `button` 兜底 → `.el-button`（换族后 `button` 会命中
  页面任意按钮=静默点错元素，全组件库唯一「命中别的元素」的选择器）；
- `save_component_selector` **保留写目标文件的非 `components` 顶层键**——旧实现无条件
  重写 `{"components": ...}`，第一次批准选择器就把 `family` 静默丢弃、平台族判据无声失效；
- `atlas doctor` 增 `ui_family` 展示项（declared/resolved/prefix_problems，**不并入 rc**——
  前缀启发式存在假阳性空间，硬拦应由生成期闸/人审决定，doctor 只如实汇报）；
- `emit_pytest` 生成期闸拒「声明族与选择器前缀不符」（先于 mkdir，零残留）。

## 3. 设计要点

- **族 = 知识数据**（`plugins/_common/knowledge/ui_family.json`），加一个新族 = 加一条
  数据、不改引擎（AGENTS §0）。`prefixes`/`has_mask`/`signature` 都是数据。
- **判据单一**（#92）：`ui_family.resolve_family` 是族判据的唯一取值口；`waits` 与
  `emit_fast` 模板同构同步，`_FAMILY_PRESENT_JS` 逐字等值由锁步测钉住（从源提取比对，
  非手抄常量）。
- **默认路径逐字节不变**：`family=None` 走原代码路径；遮罩存在即证是本族、照常等它消失；
  未声明族不猜（避免「通用包经 base_roots 带入 element-ui 族」把业务插件误报）。

## 4. 验证

- 全量：既有环境失败集与基线**逐名一致**、零新增回归；
- **真实 Chrome 取证四例**（系统 Chrome，headless，`wait_for_mask_gone` 真调）：
  纯卡片页放行 / 签名页放行 / `model-x` 页点名 / 遮罩在场等消失——全对。
  离线测试只钉了 JS 构造文本 + Python 镜像语义（无 JS 引擎），真机行为由本取证补齐；
- 锁步测：`_FAMILY_PRESENT_JS` 运行时逐字等值（`emit_fast` 模板里 `\\` 在外层**非 raw**
  字符串里塌成 `\`，写进产物后与 `waits` 同形——锁步测比的是运行时值，恰好钉住这点）；
- 写手变异取证 M1–M4（子串匹配 / 精确 token / `raise`→`return` / 退回签名判据）
  → 对应测必红 → 还原 + sha1 核对。

## 5. 已知限制（有意留白，等真有第二族平台再裁）

- **uf-5（产物只固化 element-ui）**：`emit_fast` 模板把 element-ui 的 `prefixes`
  固化进产物（产物零 import atlas，运行期读不到知识包）。非 element-ui 平台生成的
  产物会拿 `.el-` 去判 → 误报。引擎侧已数据驱动（`waits.py` 用 `fam["prefixes"]`），
  产物侧要生成期替换模板。逃生通道：引擎侧调用可显式传 `family=`。
- **生成期闸的假阳性面**：声明 `family=element-ui` 但组件选择器含 `.ant-`
  （例如 Element UI 页面嵌 ant-design 图表库）会被 fast 生成期闸硬拒。这是 fail-loud
  方向（拒生成、不假绿）、只在 `fast=True` 生效、当前无任何平台声明族；修它要造
  `also_prefixes` 这类投机机制，违「不造形式代码」。记此待真有混合栈平台时裁。
- **族表只在通用包**：平台想发明新族名须把条目加进通用包（未接平台私有
  `ui_family.json` 合并）。牵涉通用包的业务中立性，等有真实第二族平台时再设计。

## 6. 后续待办

- uf-5（产物侧族替换）——P1，等第二族平台；
- 生成期闸混合栈逃生通道——同上；
- 族表平台私有层——设计波；
- `resolve_family` 的 `declared_in_table` 已让异常文案点名「族表缺该条目」，
  doctor 侧 `family_prefix_problems` 同源点名，两者口径一致。
