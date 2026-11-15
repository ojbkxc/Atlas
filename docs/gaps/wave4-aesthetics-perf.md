# wave4 B/C 轴收尾：审美呈现面 + 性能剩余

> 2026-11-15 收口。上一收口位 2671（A 轴）→ 本波 **2797 passed / 9 failed（既有环境）/ 8 skipped**
> （verify 两次串行一致，失败逐名与基线一致、零新增）。
> 队伍 `atlas-wave4-finish`（lead + 5 写手 + verify，7 成员；glm/qwen 写手各两次流超时，
> 换 deepseek 重派后完成）。

## 1. 交付清单（按提交）

| 提交 | 条目 | 内容 |
|---|---|---|
| `a4feb3c` | ae-5 | allure 结果集人读摘要 `atlas-summary.json`（总数/通过/失败/跳过+fail_class 分布；不洗绿；既有 result 文件逐字节不变；+13 测） |
| `f6bba7f` | pf-5 | `atlas run a.yaml b.yaml` 多 flow 一次登录串行跑完（失败即停、ctx 跨 flow 重置、env/account 声明闸只作用多 flow 面、`--case-timeout` 互斥、单 flow 逐字节不变；+15 测） |
| `15757a5` | ae-6 | 产物可读性逐文件评审（纯文档；P0 四条全在 `_build_readme`：横幅嵌套加粗破裂/运行命令与意图不符/快慢形态共用段失真/死链） |
| `07e6be0` | pf-6 | 取证截图降质 opt-in（默认 PNG 保真逐字节不变，显式 env 才降质）+ 附件复制原子/并行；+23 测 |
| `3cf72ca` | ae-1~ae-4 | CLI 统一渲染层 `cli_render.py`（默认人读视图分组+verdict 行+hints、`--json` 机读唯一出口与旧 json.dumps 逐字节等值；doctor 分组+顶部 verdict；tasks/contracts/library/components 汇总行；rc2 文案逐条核对）；+18 测 |
| `213af64` | ob-2 | `config add` 骨架 envs.json 补 account/ou 占位键（ob-1 数据层缺口）；+21 测 |
| `14435b6` | pf-11 | `slots_between` 计数周期公式化 O(1)，DST/dom-dow 边界对照测（公式==逐 slot），不适用档位回退点名；+30 测 |
| `9b8c380` | pf-9/pf-4 | allure-analyze 增量读（mtime+size 指纹缓存跳过未变 result，缓存分类摘要回填、汇总与全量逐键等价；坏文件不写指纹；`--no-incremental` 逃生）+ waits 轮询节拍常量化 `TICK_MS=25`（emit_fast 模板锁步同步；只降解析延迟上限、忙判据 #36 一字不动）；+21 测 |

另有 `7675ac8`（ae-7 读者导航，纯文档）在 `docs/reader-guide.md`。

## 2. 验证（四层）

1. **全量 pytest 两次串行**：9 failed 逐名一致（既有环境失败）、零新增回归；
2. **verify 独立终验**（spark 跨族）：变异抽查（渲染层 --json 等值/多 flow 退化重登/增量读恒跳过/公式常量/摘要缺计数）、单 flow 逐字节等价、snalai untracked、中立性 grep——**裁定可提交**；
3. **写手变异取证**：各写手 M 系列（注入→必红→还原逐字节）+ lead 自做 pf-9（指纹比对恒真/新文件跳过→9 红）与 pf-4（TICK_MS=100→2 红）；
4. **过程缺陷闭环**：pf-9 首版「跳过未变→汇总归零」缺陷被测试当场抓出（第二次运行 total=0），改为缓存分类摘要回填后与全量逐键等价；`touched` 保留未变条目防缓存清空（否则第三次运行全量重读、增量形同虚设）。

## 3. 在案未做（有意留白，非遗漏）

- **ae-6 的改造**：评审已记录 P0/P1/P2（产物 README 四条 P0 全在 `_build_readme` 单函数内），改造留后续波次；
- **pf-3**（autopilot 固定 1000ms → 语义等待）：#36 口径要求真机验证失败点不移动，本机禁浏览器无法闭环，不做；
- **C12 幂等面终验核查 sound**（verify 实查）：`IDEM_WHERE_KEY="atlas_idem"` 是机制键名唯一出处
  （`delivery.py:23`），`idempotency_token` 全仓只有两个调用方（`probes.py:190/245`，均不传 scope），
  键注入单点 `probes.py:193`——无「多 namespace 撞同一条幂等记录」的面。核过即可，不构成缺陷。

## 4. 过程教训

- **流超时是提供商侧系统性现象**（glm/qwen/deepseek 全部中招，600s 无完成事件）：同一成员两次同错即停摆换模型重派；`effortLevel=max` 长 turn 更易触发，写手被叮嘱拆小步提交。
- **锁步测提取方式要随上游形态适配**：pf-4 把 waits.py 内联字面量提为常量后，锁步测的正则提取（`timeout_ms // (\d+)`）失效——适配为「常量值优先、内联字面量兼容」，等值判据语义不变。
- **钉值判据改形态判据**：`test_no_fixed_sleep_in_artifacts` 钉产物 TICK_MS=100 的数值，pf-4 改值后红——注释原意就是「形态判据非数值判据」，改回形态（`^TICK_MS = \d+\b`），非迁就实现。
- **同仓多写手 + 并发全量互踩**：verify 跑全量与 lead 跑全量并发时出现 20 failed 假回归（调度/fast_artifact 套件），单跑全绿——终验数以**串行冻结态**两次为准。
