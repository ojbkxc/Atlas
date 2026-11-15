# wave5 backlog 收口（gap14 待办 + ae-6 剩余留白）

> 范围：`gap14-ai-autonomous-test-loop.md` §4 待办表里 wave3+1 / wave3+2 / wave4 的
> 全部 en-*/ob-*/uf-*/complex-flow 条目，加 wave4 可读性评审（`wave4-artifact-readability.md`）
> 的剩余留白。**逐条给处置与提交，未做项如实留白**。
> 计数口径以 README 徽章 / AGENTS §2 为准（四处同数，lead 统一收口）。

## 1. 交付表

| 条目 | 处置（一句话机制） | 提交 |
|---|---|---|
| en-1 | `resolve_plugin()` 回落 demo 时 stderr 一行 ASCII WARN（进程级一次；显式 `ATLAS_PLUGIN=demo` 不警告）；`ATLAS_REQUIRE_PLUGIN=1` 无 ATLAS_PLUGIN 时启动期 ValueError（`config._flag_env` 单点，只认精确 `"1"`） | `b99898a` |
| en-2 | 知识条目平台维度：组件/pitfall 可选 `plugin` 标签；读侧 `_entry_visible`/`_entry_plugin`/`GENERIC_PLUGIN` 单点——plugin 缺失=可见（兼容存量）、generic 任意可见、跨平台不可见；`shadowed_components()` 点名被遮蔽项；批准写当前插件名且 generic 不被覆盖 | `4cc1dcb` |
| en-3 | 草稿 identity 首段掺 `resolve_plugin().name`（`task\|exc\|class\|pitfall` → `plugin\|…`），跨平台同任务+同异常不再合并、证据不互相顶掉；行新增 `plugin` 字段；解析失败回落空段（不阻断登记）；**存量草稿 id 会变**——写进模块 docstring 的有意行为变更 | `b99898a` |
| en-4 | `ATLAS_ENTRYPOINT` 选名从 `envs.json` `environments.<env>.entrypoints.<name>` 取 base_url（`entrypoint_urls`/`environment_config` 属性）；找不到点名拒绝、不静默回落；坏形态降级空表 | `0220b24` |
| en-5 + ob-5 | `_read_dotenv_pairs` 判据单点 + `load_legacy_dotenv` 白名单（`ATLAS_*` + 当前前缀 `USERNAME/PASSWORD/PWD_*/URL_*`），白名单外只计数 + WARN **不回显值**；runtime/legacy 改调它——旧仓 .env 不再整份 `setdefault` 进 `os.environ`（跨平台残留污染封口） | `0220b24` |
| en-6 | **已在更早波次交付（cc-6，commit 7189250）**：`atlas allure --source-dimension` opt-in 时 historyId 掺平台维度；本波未再改 | — |
| en-7 | `config.browser_proxy` opt-in（http/https/socks5）；未设置 return None=launch kwargs 逐字节同旧；坏形态启动前 ValueError；`session.py` launch 前追加 `proxy` | `0220b24` |
| en-8 | runs.jsonl 分流 opt-in `ATLAS_RUNS_SPLIT=plugin`（写侧 `recorder._split_runs_path` 精确匹配；读侧 `reporting.runs_inputs`/`_read_inputs_text` 聚合分片、缺分片回落单文件、补断行）；**allure 读侧由 lead 接线**（`6c6886b`，域外裁决见 §3） | `7e23ceb` + `6c6886b` |
| en-9 | 双入口跨入口业务流**先文档后代码**：`docs/dual-entry-flows.md` 固化「两条 flow + 共享 plan_ctx」口径与不承诺项（pf-5 多 flow 一次登录串行是已交付承接位）；不写投机代码 | `8a1c8d6` |
| en-10 | flow 级 `await: {kind: poll, timeout_ms, interval_ms?, assert}` 轮询；超时落 `AwaitingHuman`→waiting（不洗绿）；形态判据在 compile 静态段 | `8550bf6` |
| en-11 | engine `_register_readonly_replay` 只读 GET 重放接线——`api_evidence` 重放的消费侧从此有真实生产侧；`Actor.api` 缺 Ability 时抛的是 **RuntimeError**（不是 AttributeError），实现用双兜底（先 `actor.api`，失败再扫 `actor.abilities.values()` 找带 `get` 的），并加 evidence 空值早退 | `8550bf6` |
| en-12 | assert `raises: {type, match?}` 负向断言（`failures.raise_type` 白名单；控制流恒穿透不吞） | `8550bf6` |
| en-13 | `runtime.check_login_protocol` + `LOGIN_PROTOCOL` 常量：装配期检查 do_login（必填）/switch_to_ou/switch_to_lang/LOCATORS 形态，缺函数/形态坏点名而非 AttributeError 漂移 | `0220b24` |
| en-14 | exports 产物缺省目录 **root-anchored**：`<root>/exports/<kind>/<name>/`（root=项目根），从项目根执行与旧 CWD 相对逐字等价；显式 `-o` 原样使用 | `1a2e2c3` |
| en-15 | `reconcile` 对账（`failures.reconcile_diff`）：两侧 `save_as` + 字段映射 + 值级 diff，跨模块一致性核对进引擎 | `8550bf6` |
| ob-2 | 已在 wave4 交付（骨架 envs.json account/ou 占位键），本波未再改 | — |
| ob-3 | `gen --plugin X` 的 step.domain 接线：domain 走 `plugin_api.resolve_plugin()` 插件解析单点，坏插件名生成期点名不静默回落 demo | `6a3fc7f` |
| ob-4 | 通用知识包 15 组件 + 19 pitfall 打 `stack`/`family` 标签；config_add 骨架写族字段（空=未声明） | `be07096` |
| ob-6 | 产物 README 去 ISC 业务头字面量（按步骤实际 `${VAR}` 渲染）+ pages/support 可读性 | `e092c97` |
| ob-7 | doctor `platform_checklist` 子项：playbook §9 九项 ↔ `_common/domains.json` checklist 七类键映射（`PLAYBOOK_CHECKLIST_MAP` 单点）；缺文件/坏形态 `present:false` 不改 rc | `2624b43` |
| ob-8 | `docs/first-bug.md`（骨架→第一个 bug 最短路径，每步命令 + 预期 rc）+ README 导航链接 + 离线冒烟测（`tests/test_readme_quickstart.py`） | `8042c86` |
| uf-4 | **裁决：四个混合选择器不拆**（见 §3），归因钉成断言防漂移 | `4cc1dcb` |
| uf-5 + uf-6 | `.el-loading-mask` 收 `MASK_PROBE_SELECTOR` 单点（legacy_fast）；产物字节不变 | `58017ab` |
| uf-7 | `review_code._SELECTOR_PATTERN` 样例去精确清单（`\.el-table\|\.el-button` → 族中立 `\.[\w-]+`）；精确清单漏判 `.el-table__fixed-right`/`.el-loading-mask` 已探针实证 | `aafa53c` |
| uf-8 | playbook §2.10 机器可读族标签说明（`ui_family` 表 / components 顶层 `family` / `resolve_family` 取值口对齐）+ 文档漂移守卫测 | `6bf5d91` |
| ae-6 剩余 | `_atlas_fast.py` 结构导航 + 治理注记对照表（维护者/接线人分层）；conftest 删 `_atlas_fast.ENV_KEYS` 死指针、补 `__` 前缀私有键说明、`sole_page` 拆 `_launch_channel`/`_attach_channel`；pages 形态标记/models 注/`__all__` 折行/同判据注记；support 网络层大捕注记；产物测试 docstring 拆行。快照只更新确有变化项 | `e092c97` / `e6e0d34` / `86cd27c` |
| complex-flow | `examples/complex_flow_demo.yaml`：dispatch 路由（`on: order_resp.data.total`）+ UI 写腿（click_new/click_save）+ find_only 数据供给；写腿两级拦截面（engine 生产写闸 `ATLAS_ALLOW_PROD_WRITE=1` / 调度登记 `--i-accept-writes`）写进示例头；compile rc0 + 写腿集合钉测 | `0b82cf6` |
| pf-3 | autopilot `action.target` 语义等待（mask_gone/rows/vue_field/url_change 全委托 `waits.*`）；不写 target 旧行为逐字节不变；**真机取证通过**（见 §3） | `fa47f44` |

## 2. 判据规模

- 起点 2805 passed → 收口 2940 passed（**+135 测**），collect-only 2957；
- 全量判据 = `9 failed 逐名一致（既有环境基线）+ 零新增`，见 §5；
- 新增测分布：`test_plugin_api_en1.py`(9) / `test_experience_plugin_dim.py`(3) /
  产物与知识面（en-2/ob-4/ob-6/ae-6/uf-5/6）**+** 环境面 17 / 引擎面（en-10/11/12/15）/
  CLI 面（en-14/ob-7）/ 报告面（en-8 读侧）/ complex-flow 2 / pf-3 真机脚本。

## 3. 裁决记录

1. **uf-4 四个混合选择器不拆**（`.el-pagination button.btn-next` / `.edui-editor` /
   `.el-picker-panel:visible td.available` / `.el-pagination li.active`）：
   主体 `.el-` 段已由 wave4 A 轴的族前缀存在性判据兜住；拆 per-family 需要真实非
   Element 平台页面取证，否则就是红线 #12 的形式代码。**留待换族真机取证**。
   `test_uf4_non_el_framework_tokens_not_family_expressible` 把当前归因钉成断言——
   将来要拆时它会先红提醒。
2. **en-8 allure 读侧由 lead 接线**（`6c6886b`）：writer-artifacts 的文件域是
   reporting/recorder，`allure_report.emit_allure` 读原文的接线跨域；读侧判断委托
   `reporting._read_inputs_text`/`runs_inputs` 单点，不复制第二把分片聚合尺（#92）。
3. **pf-3 真机取证**（`scripts/pf3_realmachine_verify.py`，Chrome channel 157 headless，
   本地 `set_content` 零凭据）：S1 mask 800ms 后消失 → 语义等待 963ms 返回（不是固定
   1000ms）；S2 无 target 固定睡 1000ms（旧行为逐字节）；S3 mask 永不消失 → 抛
   TimeoutError（不洗绿）。**夹具坑**：注入 mask 必须显式定尺寸
   （`position:fixed;inset:0;width:100vw;height:100vh`），否则高度 0 → Playwright 判
   hidden → `wait_for_selector(state="hidden")` 立即满足——与 CLAUDE FAQ「空 `.el-table`
   高度 0」同族，离线 fake 测不出。
4. **complex-flow 写腿两级闸互不替代**：`ATLAS_ALLOW_PROD_WRITE=1` 是运行轨放行键
   （放行时 ASCII 警告 + runs.jsonl 记 `prod_write_allowed` 可审计）；
   `--i-accept-writes` 只作用于调度登记（承认台账里挂写 flow），调度派发的子进程照样
   受 engine 写闸管。示例刻意不绕闸。

## 4. 未做项（如实留白）

- **uf-4 的四个混合选择器**：不在本波拆（理由见 §3.1），不是漏做。
- 其余 gap14 表内条目（en-1 ~ en-15 / ob-2 ~ ob-8 / uf-1 ~ uf-8 / complex-flow）**已全部有处置**；
  en-6 / ob-2 / uf-1/2/3 是更早波次已交付，本波只核对未再改。

## 5. 验证边界（如实）

- 离线全量：`9 failed / 2940 passed / 8 skipped`（collect-only 2957），9 条逐名与既有基线一致
  （`test_emit_compat×3` / `test_gen::test_with_pytest_no_allure_rc2` /
  `test_init_scaffold::test_allure_e2e_offline` / `test_legacy_compat_xcheck×2` /
  `test_migration_sentinels×2`——环境性既有失败，本波未修）。
- **真机面只有 pf-3 一项**（零凭据本地页）；en-4 多入口、en-7 代理、en-13 登录协议
  预检、en-11 重放接线均只有离线判据，真实平台行为未取证（各条目测试已钉形态与
  失败路径，不冒充「真机已验证」）。
- `atlas compile examples/complex_flow_demo.yaml` rc 0 实测；示例的真实执行需 SIT
  环境与写放行，本波未跑（不声称跑过）。

## 6. 过程教训

1. **502 是系统性故障不是成员问题**：`API Error: 502 System proxy bridge failed`
   （direct: connection refused 104.223.65.202:10143）在 glm/deepseek/sensenova
   三族模型上逐成员反复发作。处置纪律=同一成员两次同错即停摆、换人/lead 接手；
   写手「每条一提交」，避免一次打断损失整批。本波 writer-env 四条最终由 lead 在
   main 工作树完成；writer-plugin 停摆后 en-1/en-3 亦由 lead 接手。
2. **qwen3.8-flash 不可用**：writer-env 两次同错即停用（不重试第三次）。
3. **TeamPlan 运行中不可 replace**：返回「Only a draft or pending review can be edited」，
   运行中计划改不了模型；模型选择必须在提交前定。
4. **独立会话 worktree 在 502 下同样不可靠**：`atlas-wave5-env` 会话三次 502 后弃用，
   改为 lead 在主工作树串行消化——本波结论：代理故障期间「少而稳的写手」优于「多而脆的并行」。
5. **成员缺陷两处由 lead 代修**（已含进提交）：en-11 测试导入错（`atlas.evidence` 不存在
   → `atlas.session.ApiEvidence`）；en-11 实现真缺陷（`Actor.api` 缺 Ability 时抛
   RuntimeError，`getattr` 接不住 → 双兜底）。
