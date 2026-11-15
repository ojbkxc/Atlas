# 双入口跨入口业务流：口径固化（en-9，先文档后代码）

> 状态：**文档口径，不写投机代码**（en-9 立项原文：「双 context 或『两条 flow + 共享
> plan_ctx』口径固化，先文档后代码」）。本文固化后者——已交付承接位是 pf-5 多 flow
> 一次登录（`atlas run a.yaml b.yaml`），不新增引擎机制。
> 取舍记录见 `docs/gaps/wave5-toolface-notes.md`。

## 1. 事实面：单会话单页单 base_url

Atlas 的执行核是**一个会话、一个浏览器页、一个 base_url**（`DemoRuntime`：一次
Chromium launch + 一次登录 + 一份 storage state）。同系统的两个入口（admin 后台 +
seller 商家端）如果 base_url 不同，当前引擎**没有**「双 context 同时活跃」的承接位
——这是刻意的边界，不是遗漏：双 context 意味着双登录态、双凭据面、双取证目录，
把「会话归属」从约束变成猜测（哪条请求发往哪个会话？runs.jsonl 归谁？），收益盖不过
归因模糊的代价。

## 2. 承接位：两条 flow + 一次登录串行（pf-5，已交付）

同系统双入口的**串行**跨入口业务流，现形态是两条 flow 一次登录跑完：

```bash
atlas run flows/admin_part.yaml flows/seller_part.yaml
```

- 整批共用一个 DemoRuntime：一次 launch、一次登录，逐条 flow 串行执行，结束后统一
  关闭（`cli._run_multi_flows`）；
- **会话归属硬闸**：flow 顶层可声明 `env:` / `account:`（非空字符串），与本次运行
  实际值不一致即拒绝（`_multi_flow_session_gate`）——一次登录只对应一个环境一个账号，
  声明与实际不符时绝不静默执行（不静默用错账号跑真实系统）；
- 失败即停：一条 flow 失败，后续 flow 不执行（没有「部分通过」的批次终态）；
- 回收清单合并：整批建过的单统一落 `reports/recycle_list.jsonl`。

## 3. plan_ctx 的边界：跨 flow 交接什么、不交接什么

`plan_ctx.json` 是**产物侧**（`export-pytest` 快包）的跨批上下文文件，engine 轨的
`atlas run a.yaml b.yaml` 不使用它——两条 engine flow 之间的数据交接只有两条合法通道：

1. **同一 flow 内**：step 的 `save_as` / `requires`（引擎 ctx，跑完即弃）；
2. **跨 flow 跨批次**：数据供给显式化——上一轮的产出落盘（`reports/`、回收清单），
   下一轮 flow 用 `requires` 的 `where` 显式查回，或人填进 env/flow 参数。

**不承诺项（写死，避免误读）**：

- 两条 engine flow 之间**没有**内存级 ctx 传递（ctx 不跨 flow 存活）；
- plan_ctx 白名单标量机制**只在产物包**里成立，不外溢到 engine 轨；
- 双入口**并行**执行（两个浏览器同时活跃）不在本口径内——那是「双 context」的
  立项范围，本波明确不做；
- 跨入口**不同 base_url** 的两入口串行，仍要求两次独立 `atlas run`（各自 env 键各
  自登录）——pf-5 批次内共享的是同一个 base_url 的会话；envs.json 多入口坐标
  （en-4，wave3+2 已由环境面批次落地）解决的是「报错点对哪个入口」的归因，
  不是会话共享。

## 4. 什么时候该拆两条 run

| 场景 | 用法 |
|---|---|
| 同 base_url、同账号、一次登录能到的串行业务流 | `atlas run a.yaml b.yaml`（pf-5） |
| 跨 base_url 的双入口 | 两次独立 `atlas run`，各自 env；数据交接走显式落盘 |
| 双入口并行作业 | 本口径不支持；需要时是独立立项（双 context 形态），先过会话归属/凭据面设计 |
| 产物快包跨批复用上下文 | plan_ctx（白名单标量），见 `export-pytest` 产物 README |

## 5. 与审核塔的关系

跨入口业务流跑绿后走 flow 级 `harvest: true` / `atlas run --harvest` 固化——固化的是
**单条 flow** 的动作序列，pf-5 批次不产生「批次级」资产；跨入口串行业务流要沉淀，
按入口各固化各的（两条 flow_candidate），人审后进公共任务库。这保持了「知识写路径
只有 review approve」的红线（#9/#10）。
