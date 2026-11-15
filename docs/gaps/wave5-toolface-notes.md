# wave5 工具/文档面落地注（en-14 / ob-7 / ob-8 / en-9 / complex-flow / uf-7 / uf-8）

> 范围：wave5 收尾中「工具/文档面」批次的取舍记录。P0 的 ob-3 另见提交 6a3fc7f
> （`gen --plugin X` step.domain 接线）；本文件只记批次内其余条目。

## en-14：exports 产物目录 root-anchored

**事实**：旧缺省 `exports/...` 是 **CWD 相对**。换平台/换目录执行时同名 flow 产物互相
覆盖（同一路径撞 `--force` 闸）或散落（不同 CWD 各落一份）——产物含真实 URL/账号名，
散落到陌生目录既是噪音面也是泄漏面。

**裁决：root-anchored，不按插件分子目录**。缺省 = `<root>/exports/<kind>/<name>/`
（kind ∈ {gen, pytest}，root=AtlasConfig.from_root 的项目根：`ATLAS_ROOT`/`--root`/
`.atlas-root` 向上探测）。理由：

- 按插件分子目录会把「产物在哪」和「平台身份」耦死——产物是 flow 的产物不是插件的，
  换平台重导出同一 flow 时分子目录反而制造第二处散落；
- root-anchored 保留了「一棵 exports 树」的既有习惯，`--force` 覆盖闸继续在同一目录
  生效（覆盖检查 = provenance 面不变）。

**兼容面（写清取舍）**：从项目根执行时 root==CWD，与旧 CWD 相对路径**逐字等价**——
README「拿到先跑这三步」的 `-o exports/...` 示例、既有脚本零改动。无 `.atlas-root`
的裸目录里 root==CWD，同样等价。显式 `-o` **原样使用**（相对路径按 CWD 解析是用户
显式选择，不被 root 改写）。

**判据测**：`tests/test_cli_exports_root.py`（4 条；CWD 与 root 分离的反向对照——
CWD 侧零散落；变异取证：回退 CWD 相对实现时 `test_export_pytest_default_dir_root_anchored`
必红）。

## ob-7：doctor 接入清单（playbook §9 → _common/domains.json）

**事实**：`_common/knowledge/domains.json` 的 `checklist`（通用包版新平台接入清单）
此前**零消费方**——清单只活在文档里，换平台的人不会碰它；doctor 也不覆盖 playbook
§9 的检查项。

**落地**：doctor 新增 `platform_checklist` 子项（JSON 面 + text 面 `[接入清单]` 组）：

- 判据唯一实现在 `atlas.knowledge.platform_checklist`（#92）：读顺序
  `kb.load_composed("domains")` → 通用包 `common_knowledge_root()/domains.json`
  （demo 的业务坐标文件也叫 domains.json，无 checklist 键 → 视同没声明落到通用包；
  平台插件可用同名文件整值覆盖）；
- `PLAYBOOK_CHECKLIST_MAP`：§9 复选框（网络/登录/技术栈/入口/接口面/数据卫生/
  能力边界/权限/可复原性 = 9 项）↔ checklist 七类键的映射单一真相；
  `stack_entry_and_accounts` 一类对应技术栈+入口+权限三项（原论文就是一组开工前
  确认，拆开丢「先备账号再开工」的次序）；
- 口径：缺文件/坏形态如实 `present:false` **不改 rc**（与 boundary_ledger 缺失同
  口径——缺清单 ≠ 清单漂移，两个事实分开陈述）；只读、离线、纯展示；
- `cli_render.py` 属 doctor 渲染面（本批次获准随 doctor 分支一起改）。

**判据测**：`tests/test_state_reporting_cli.py` 4 条（§9 九项逐条映射钉死；坏 JSON/
非映射如实回落不抛 #89 家族；平台声明优先于通用包）。变异取证：删映射里一行 →
`test_cli_doctor_platform_checklist_playbook_nine_sections` 必红。

## ob-8：骨架 → 第一个 bug 最短路径

`docs/first-bug.md`（每步命令 + 预期 rc）+ README 链接（不动计数位）+ 离线冒烟测
（`tests/test_readme_quickstart.py`，不联网不用凭据）。见该文件与提交。

## en-9：双入口跨入口业务流（先文档后代码）

`docs/dual-entry-flows.md` 固化「两条 flow + 共享 plan_ctx」口径与不承诺项（pf-5
多 flow 一次登录串行是已交付承接位）；不写投机代码。

## complex-flow：复杂业务流脚手架

`examples/complex_dispatch_flow.yaml`（dispatch 路由 + 写腿示范，写腿默认被生产写闸
拦下 env_blocked，文档说明 ATLAS_ALLOW_PROD_WRITE=1 人工放行）+ `atlas compile` 判据测。

## uf-7 / uf-8

- uf-7：`review_code._SELECTOR_PATTERN` 的 `\.el-table|\.el-button` 字面样例 →
  族中立类选择器 `\.[\w-]+`（原样例连 `.el-table__fixed-right`/`.el-loading-mask`
  都漏判——exact-list 探针实证）；#id/[attr]//xpath/html 结构面不变。
- uf-8：playbook §2 补机器可读族标签说明（与 `ui_family` 表 / components 顶层
  `family` 字段对齐）。
