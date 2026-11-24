# 骨架 → 第一个 bug：最短路径（ob-8）

> 目标读者：拿到 Atlas 想在**自己平台**上跑出第一条缺陷记录的人。每步给命令与预期 rc
> （exit code 口径：0=成功，1=体检发现漂移，2=用户输入错误，3=等人/结构自检，124=超时）。
> 离线段（步骤 0–7）不联网、不用凭据；联网段（步骤 8 起）需要目标平台的环境坐标。
> 配套：`docs/reader-guide.md`（全文导航）、`docs/functional-testing-playbook.md` §9
> （换平台开工检查清单——`atlas doctor` 的 [接入清单] 组会把它映射出来）。

## 0. 生成骨架（离线）

```bash
atlas init myproj --system <系统名> --module <模块名>    # rc 0；.atlas-root 最后落盘
cd myproj
```

骨架内容：`.atlas-root`、`.env`（值全空）/`.env.example`、`pyproject.toml`、
`flows/smoke.yaml`、`tests/test_smoke.py`、`scripts/run_allure.py`、README。
rc 2 = 目录已有 `.atlas-root` 或非空且未加 `--force`——换目录，别覆盖。

> Atlas 源码分发：init 会探测 Atlas 仓库根把 `atlas-test-platform` 钉成 `file://` 依赖；
> 探测失败打 `WARN:` 两行补救路径（`pip install -e <Atlas 仓库>` 或
> `atlas init --atlas-path <Atlas 仓库>` 重新生成）。

## 1. 安装（联网一次）

```bash
pip install -e .                       # rc 0
python -m playwright install chromium  # 只跑 API 腿可跳过；跑 UI 腿/骨架 pytest 必需
```

## 2. 体检（离线）——预期 rc 1，会告诉你缺什么

```bash
atlas doctor            # rc 1 属预期：新骨架没有旧仓资产/没有 base_url
atlas doctor --format text   # 人读版：每组末尾 hints 点名缺哪个键/文件
```

rc 1 的三个常见缺项（hints 会逐条给）：

| 缺项 | 补法 |
|---|---|
| `base_url=unset` | 把 `<PREFIX>URL_<ENV>` 键写进 `.env`（playbook §9 第 1 项） |
| `legacy_pages` 缺失 | 无旧仓资产的平台**不需要**——它只影响 doctor 的 rc，不挡 API 腿 |
| **`demo_api` 缺失** | **会挡 `atlas run`（连纯 `api_call` flow 也挡）**——见下方第 4 条；doctor 的 rc 只是它的第一面 |
| `knowledge=builtin` | 设 `ATLAS_KNOWLEDGE_DIR` 指私有知识库（读回退包内虚构默认，批准写路径必须私有） |

**关于 `demo_api`（2026-11-24 实测更正，别照旧文档猜）**：新骨架没有旧仓 API 项目时，
`atlas run flows/smoke.yaml --no-login` 会在**开浏览器之前** rc 2 退出：

```
FileNotFoundError: 找不到 DemoApi 适配目标：<root>/../legacy-api/scripts/demo_api.py
```

原因不是「配置写错」，而是**当前装配口径**：`DemoRuntime.__enter__` 无条件
`attach_demo`，而 `precheck_session_inputs` 的 `check_demo_api` 把这条失败**前移**成
fail-fast（#103：旧实现会先起 Chromium、走真实登录、白烧会话才撞缺文件）。
这条边界已登记在 `docs/gaps/wave5-backlog.md`（en-5/ob-5 行末「`attach_demo` 仍无条件
装配」，根治需插件 API seam，属未解决设计项）。

现在能做的两件事（**不要**指望跳过预检让它跑起来——那只是把失败推后到 `attach_demo`）：

1. 设 `ATLAS_DEMO_API_PROJECT` 指向含 `scripts/demo_api.py` 的目录（旧仓或自建桩）；
2. 或先用**不依赖 DemoRuntime 装配**的离线面验证流程结构：`atlas compile` →
   `atlas gen --with-pytest`（产物零 `import atlas`，可独立运行）。

## 3. 离线跑通最小闭环（不联网）

```bash
atlas tasks                          # rc 0：可用 Task 清单（requires/produces）
atlas compile flows/smoke.yaml       # rc 0：骨架 flow 结构合法
atlas review                         # rc 0：审核塔空塔
atlas gen <接口文档>.md --check-only # rc 0：文档能解析、能出 flow（不落盘）
```

任一步 rc 2 = 输入问题（路径/格式），stderr 一行点名；照着改，不要猜。

## 4. 从接口文档生成测试代码（离线）

```bash
atlas gen <接口文档>.md                       # rc 0，产物默认 <root>/exports/gen/<文档名>/
atlas compile exports/gen/<文档名>/flows/*.yaml   # rc 0：生成的 flow 结构合法
```

- `--with-pytest` 额外产可独立运行的 pytest 包；`--fast` 须配它（单用 rc 2）。
- 字面鉴权值会被换成 `${ATLAS_HDR_<n>}` 占位（值不进产物），人审后把值写 `.env`。
- 生成**只产出不执行**（红线 #9）；`--to-tower` 把 trace 草稿送 `atlas review`。

## 5. 跑真实平台（联网；第一个 bug 通常在这里出现）

```bash
# .env 里补齐平台坐标后：
atlas run flows/smoke.yaml             # 0=全绿 / 2=输入错误 / 3=等人 / 124=看门狗超时
atlas summary                          # 聚合 runs.jsonl；attention 块点名等人/塔积压
```

第一个「失败」先过三道判读（细节见 `docs/api-pitfalls.md`）：

1. **环境还是缺陷**：`env_blocked`（缺配置/生产写闸/配额 0）不是代码缺陷——
   按 reason 点名补键；写腿默认被生产写闸拦下（放行键 `ATLAS_ALLOW_PROD_WRITE=1`，
   放行必留痕）。
2. **会话还是接口**：200 但 body 是整页 HTML = 会话过期重定向；401/403 先查认证头
   格式（playbook §11.4），再谈越权。
3. **归因看分类不看直觉**：`atlas summary` 的 fail_classes 分布 +
   `reports/runs.jsonl` 的 step 记录（请求实参/响应快照/截图）。

> 骨架冒烟测试 `pytest -q` 跑本地页面不访问真实系统（需 chromium 已装）；
> 真实业务用例写进 `flows/` 后用 `atlas run` 执行。

## 6. 沉淀第一个发现

```bash
atlas review                                  # rc 0：塔里看失败草稿/证据卡片
atlas review --approve <id> --remedy "..."    # 批准进知识（写路径只有这一条，红线 #9/#10）
atlas review-code tests/ --fail-under 75      # rc 0/1：代码质量门禁（CI 可用）
```

失败草稿、探索候选、trace 编译全部汇进同一个塔；`--reject --reason` 留痕不删。

## 7. 常见第一步卡点速查

| 症状 | 归因 | 处置 |
|---|---|---|
| `atlas doctor` rc 1 但不知道缺啥 | 诊断面没读 hints | `--format text` 每组末尾 hints 逐条给键名 |
| `pip install -e .` 解析不到 `atlas-test-platform` | init 未探测到 Atlas 源码根 | `atlas init --atlas-path <Atlas 仓库>` 重新生成 |
| `atlas run` 报空账号 rc 2 | 账号三层来源全空 | `ATLAS_SESSION_*` env > 私有 `session.json` > envs.json 环境坐标 |
| 接口全回 BUG-920 | 缺业务识别头 | flow `headers` 带 `isc-app`（gen 已自动并入） |
| `atlas gen` rc 3 | 生成器缺陷（结构自检失败） | 带文档样例报 issue，不要改产物凑合 |
