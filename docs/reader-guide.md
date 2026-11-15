# Atlas 读者导航

> 本页只做**索引与分流**：按「你是谁、要干什么」指到具体文档，各文档给谁看、从哪开始。
> 内容一律在原文——本页不重写、不新增口径。验证计数等收口数字一律看
> [README「当前验证边界」](../README.md)（lead 统一收口，四处同数）；FAQ 口径以
> [CLAUDE.md「常见问题」](../CLAUDE.md) 为准。
> 新增/移动文档后请在本页补一行（AGENTS §5 文档维护义务）。

## 文档全景

| 文档 | 一句话定位 | 主要读者 |
|---|---|---|
| [README.md](../README.md) | 产品总览 + 命令面 + 验证边界；信息密度高、按波次追加，先当入口再按节查 | 所有人（入口） |
| [CLAUDE.md](../CLAUDE.md) | 开发者速览：常用命令表 / 关键模块表 / 开发规范 / 常见问题（FAQ） | 上手 + 运维 + 贡献 |
| [AGENTS.md](../AGENTS.md) | 硬约束：Never 红线 / 验证纪律 / 架构不变量 / Ask first / 文档维护；冲突时以它为准 | 贡献 + 判据 |
| [PRIVACY.md](../PRIVACY.md) | 数据 / 凭据 / 运行产物 / 报告分发 / 外部服务边界 | 所有人（碰数据前） |
| [docs/api-pitfalls.md](api-pitfalls.md) | 接口失败现场判读手册：症状 → 根因 → Atlas 处置位置 | 运维 + 贡献 |
| [docs/functional-testing-playbook.md](functional-testing-playbook.md) | 跨平台功能测试作业手册：流程 + 跨平台不变的判据（误报教训固化） | 贡献（换平台施工） |
| [docs/element-ui-generic-knowledge.md](element-ui-generic-knowledge.md) | Element UI 通用知识包分类台账：逐条通用性判定与去向 | 贡献（知识提炼） |
| [docs/gaps/*.md](gaps/) | 各波次 gap 设计 / 落地记录（命题、方案、判据与边界） | 贡献 |
| [docs/gaps/impl_pi/*.md](gaps/impl_pi/) | pi 吸收波施工级设计（总纲 → 对抗评审 → 分主题设计 → 终验） | 贡献 |
| [docs/gaps/research_pi/*.md](gaps/research_pi/) | 吸收前的盘点与调研（现状对照 / 机制盘点 / 模式提炼） | 贡献（背景） |
| [docs/ownership/atlas-boundaries.json](ownership/atlas-boundaries.json) | 边界账本（数据）：引擎消费、债标价与 decision_log；引擎运行时不解析 | 贡献 + 判据 |

---

## 1. 上手（第一次跑 Atlas）

**目标**：不碰真实系统、零凭据，把 Atlas 装起来并看到核心产出。

**从哪开始**（按顺序）：

1. [README.md「拿到先跑这三步」](../README.md) —— 三步零凭据路线：装本体 →
   `atlas export-pytest` 看产物 → `atlas init` 建骨架跑冒烟。
2. [README.md「快速开始」与「新建 Atlas 项目」](../README.md) —— 命令面（旧套件原样跑、
   新项目骨架、gen/export-pytest 等）。
3. [README.md「配置」](../README.md) —— 环境键（URL / 凭据 / 私有知识库路径）。
4. [CLAUDE.md「常用命令」](../CLAUDE.md) —— 全量命令速查表。

**常见下一步**（FAQ 口径见 [CLAUDE.md 常见问题](../CLAUDE.md)）：

- 接真实项目：「仓库是通用 demo，怎么接回公司真实项目？」（`ATLAS_*` 映射，不改引擎）。
- 真实会话值：「测试已经覆盖真实 SIT 吗？」（离线全绿 ≠ 能力可用）。
- 私有知识：「真实内部知识/BU 坐标放哪？」（`ATLAS_KNOWLEDGE_DIR`，包内零改动）。
- 随仓样例：[examples/](../examples/) 下 `api_sample.md`、`atlas_portable_smoke.yaml` 等，
  README 三步直接用到。

---

## 2. 运维（日常巡检 / 排障）

**目标**：跑定时巡检、判读失败现场、维护知识与审核塔。

**从哪开始**（按场景）：

- **定时巡检**：[README「定时巡检接线（C1 调度核）」](../README.md) —— `atlas schedule`
  全套（add/run/status/crontab）；没有守护进程，接 OS cron/schtasks。
- **失败现场判读**：[docs/api-pitfalls.md](api-pitfalls.md)（接口类）+
  [README「数据与失败口径」](../README.md)（失败分类、操作/环境终态家族）。
- **「上周能跑今天炸」**：[README「数据与失败口径」](../README.md) 的版本三元组
  + [CLAUDE.md FAQ「上周能跑今天炸怎么定位」](../CLAUDE.md)（`atlas doctor` / `atlas summary`）。
- **审核塔（人审是知识写回的唯一通道）**：[README「真实混用与 DSL」审核塔段](../README.md)
  + [CLAUDE.md FAQ「失败草稿和探索候选谁来审」](../CLAUDE.md)、「跑绿的用例怎么沉淀成可复用资产」。
- **旧仓 / 快脚本**：[CLAUDE.md FAQ「想让旧套件『一行不改旧仓代码』就变快」](../CLAUDE.md)
  与「Atlas 产出的脚本能不能像旧套件那样快跑」+ [docs/gaps/gap11-legacy-fast-compat.md](gaps/gap11-legacy-fast-compat.md)
  （机制、边界与已撤回的产能结论，以文内口径为准）。
- **知识库维护**：[CLAUDE.md FAQ「真实内部知识/BU 坐标放哪」](../CLAUDE.md)
  + [docs/ownership/atlas-boundaries.json](ownership/atlas-boundaries.json)（边界债目）。
- **证据与回收**：[README「真实混用与 DSL」](../README.md) 的 runs.jsonl / Allure / recycle 各段。

---

## 3. 贡献（改引擎 / 加插件 / 吸收资产）

**目标**：改引擎、加业务插件、吸收外部资产，且不破坏红线。

**从哪开始**（动手前必读，按顺序）：

1. [AGENTS.md](../AGENTS.md) —— §0 定位、§1 Never（红线）、§3 架构不变量（判据单点清单）。
2. [CLAUDE.md](../CLAUDE.md) —— 「开发规范」+「关键模块」（模块与判据归属表）。
3. [AGENTS.md §2 验证纪律](../AGENTS.md) —— 改动三连（pytest / compileall / doctor）、
  「离线全绿 ≠ 能力可用」、吸收/merge 前分类义务。

**主题索引**：

- **设计先行**：[docs/gaps/](gaps/) —— 按波次读对应 gap：
  `gap1-2-init-config`（init/config add）、`gap3-doc2code`（atlas gen）、
  `gap4-code-review`（review-code）、`gap5-allure`（Allure 集成）、
  `gap7-regression-code-eval`（产物形态评审）、`gap8-allure-compat`（产出侧对齐）、
  `gap9-fast-pytest-runner` + `gap9b-flow-export`（快模式产物）、
  `gap12-llm-autonomous-explore`（自主探索）、`gap14-ai-autonomous-test-loop`（AI 闭环落地）、
  `wave4-atomic-io`（落盘原子化）、`wave4-ui-family`（UI 族可移植面）。
- **pi 吸收波施工设计**：[docs/gaps/impl_pi/](gaps/impl_pi/) —— 一期总纲
  （`design-plan.md`）→ 对抗评审（`design-review.md`）→ 分主题设计
  （C1 调度核 / C2 证据三元组 / C3 迁移 / C4 凭据泄漏面 / C5 边界账本 / C6 版本三元组）
  → 终验（`final-verification.md`）；二期同构（`design-plan-p2.md` / `design_c9` / `design_c12` /
  `design-review-p2.md` / `final-verification-p2.md`）。
- **加插件（第二个公司/业务）**：[CLAUDE.md FAQ「新增第二个公司的业务怎么做」](../CLAUDE.md)
  —— 新建 `plugins/{domain}`，引擎不重写。
- **知识提炼**：[docs/element-ui-generic-knowledge.md](element-ui-generic-knowledge.md)
  + [docs/functional-testing-playbook.md](functional-testing-playbook.md)
  （§16「通用知识包与平台包的分工」；跨平台判据只进通用包）。
- **吸收外部资产**：[README「资产吸收与边界账本（C5）」](../README.md)
  + AGENTS §2 的 `boundary_classify` 义务。
- **背景调研**：[docs/gaps/research_pi/](gaps/research_pi/)（吸收前的盘点，防重复造轮子）。

---

## 4. 判据（红线与设计口径）

**目标**：红线、不变量、判据单点——改动与评审的尺子。

**从哪开始**：

- 两条**不可谈判红线**（全波次恒定）在 [AGENTS.md §0/§1](../AGENTS.md)：
  ① 绝不为变绿放宽断言、吞异常、把失败改 SKIP；② 凭据绝不落盘。
- [AGENTS.md §3 架构不变量](../AGENTS.md)：判据**单一实现**清单（`locators` /
  `credential_gate` / `delivery` / `versioning` / `boundaries` / `atomic_io` /
  `failures.OPERATIONAL` / `emit_fast` 等）——消费方只许调，不得复制第二把刀。
- [README「数据与失败口径」](../README.md) —— 失败分类、操作/环境终态家族口径。
- [docs/ownership/atlas-boundaries.json](ownership/atlas-boundaries.json) —— 边界账本数据
  （accepted 债必须带 decision_log）。
- [docs/gaps/wave4-ui-family.md](gaps/wave4-ui-family.md) —— UI 族判据（换平台的静默失效面）。
- **lead 裁决文件（约束性，与正文冲突处以裁决为准）**：
  [docs/gaps/gap9-lead-rulings.md](gaps/gap9-lead-rulings.md)、
  [docs/gaps/gap11-lead-rulings.md](gaps/gap11-lead-rulings.md)。
- [docs/gaps/gap11-slimming-review.md](gaps/gap11-slimming-review.md) —— 全仓冗余/臃肿审计
  （只审不改，落 #24 前一切删/合并不生效）。

---

## 维护约定

- 本页只索引、不重写；每行 = 一个文档 + 一句话定位 + 读者。
- 新文档落位后在本页补一行；文档删除/改名时同步删改对应行。
- 验证计数、验证边界等收口内容不进本页（四处收口位由 lead 统一改，见
  [AGENTS.md §2](../AGENTS.md)）。
