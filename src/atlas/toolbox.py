"""toolbox 是感知器官，不替代 Playwright 执行底座。"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Protocol

def _unwrap_records(payload: Any, source: str) -> list:
    """桥接响应提取记录列表的判据单一真相（#140，#92 杜绝两函数防线强度漂移）。

    合法形态如实放行：裸 list 原样；dict 取权威键 entries、缺失（is None，不是真值
    回落）再兼容旧 fake 的 items；空列表如实 []——「本轮无抓包/无报错」是业务事实
    不是异常（#115：归因修复不得把正常路径翻成错误）。
    坏形态点名拒绝：两键皆无的 dict（bridge 错误应答 {"status":"error",…}）、
    纯文本「错误: …」、None 与非 list 值。旧实现 learn_contracts 把这些全部折叠成
    [] 再静默登记 0 条——调查者读成「契约都学了」，实为感知器官坏了（#102 静默假绿）；
    同一形态 console_errors 在 #105 已点名 RuntimeError，两函数判据漂移=改一漏一。
    """
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        records = payload.get("entries")
        if records is None:
            records = payload.get("items")
        if isinstance(records, list):
            return records
        raise RuntimeError(
            f"{source} 返回 dict 但无 entries/items 列表（感知器官异常形态）："
            f"{str(payload)[:200]!r}")
    raise RuntimeError(
        f"{source} 返回了非预期形态 {type(payload).__name__}，拒绝伪装成"
        f"「无数据」：{str(payload)[:200]!r}")

class ToolboxBridge(Protocol):
    def call(self, method: str, params: dict | None = None) -> Any: ...

class WebToolboxSensor:
    def __init__(self, bridge: ToolboxBridge, contracts=None):
        self.bridge, self.contracts = bridge, contracts

    def list_requests(self, **filters) -> Any:
        return self.bridge.call("net_list", filters)

    def get_request(self, request_id: str) -> Any:
        return self.bridge.call("net_get", {"id": request_id})

    def replay(self, **filters) -> Any:
        return self.bridge.call("chain_replay", filters)

    def snapshot(self) -> Any:
        return self.bridge.call("page_snapshot")

    def learn_contracts(self) -> int:
        """把当前抓包里的 200 响应登记为 minimal（存活级）契约：只校验接口可达、响应可解析。

        不自动升到 high/core：high 要求 data 为对象，会把返回数组的列表接口
        （本项目支持 `data.0.paymentTerms` 这类点路径）误判成失败。契约分级纪律是
        「新增接口默认落 minimal，只有业务关键接口经人审才升级」——这里只做存活级登记，
        真正的分级升级仍走人审写私有 contracts 库，不盲目把 数百条接口变成强 schema。
        """
        if self.contracts is None:
            raise RuntimeError("未配置 ContractRegistry")
        from .contracts import Contract
        captured = self.list_requests()
        # 真实 web-toolbox 的 net_list 返回 {"total", ..., "entries": [...]}（权威键名 entries），
        # 不是 {"items": ...}——旧实现只认 items，真实形态下 captured.get("items", captured)
        # 拿到整个 dict，isinstance(dict, list) 恒 False，for 循环空转，静默登记 0 条。
        # 离线 fake 用 {"items": ...} 掩盖了这个「离线全绿 ≠ 真实能用」。entries 优先、items
        # 兼容旧 fake、裸列表兼容直接返回列表的桥接实现。
        # #140：错误形态（bridge 错误 dict / 纯文本错误）旧折叠成 [] 后静默返回 0，
        # 与 console_errors 同形态点名拒绝漂移——判据收敛 _unwrap_records 单一真相。
        requests = _unwrap_records(captured, "net_list")
        # #120 面B：真实 net_list 分页——默认 limit=50，total 报全量（v1.1.4 实测 231 条
        # 抓包只回前 50）。旧实现拿一页登记一页零告警：部分结果伪装全量完成，用户以为
        # 「契约都学了」，远处接口缺契约静默不校验（#102 静默 0 条同族假绿）。
        # total 与页大小对不上就点名终止——调用方该带 limit/offset 重取，而不是收残缺。
        # #140：解包已先于本闸门——{"total":231} 无 entries 时点名「无 entries/items
        # 列表」（真因键缺失），不再被误报成「分页截断」（#90 归因错位顺带消除）。
        if isinstance(captured, dict):
            total = captured.get("total")
            if isinstance(total, int) and total > len(requests):
                raise RuntimeError(
                    f"net_list 分页截断：抓包共 {total} 条，本次只取回 {len(requests)} 条，"
                    f"拒绝把残缺页登记成全量（用 list_requests(limit=…) 取全后重试）")
        before = len(self.contracts.contracts)
        for item in requests:
            if not isinstance(item, dict):
                continue
            method, path = item.get("method", "GET"), item.get("path", "")
            if path and item.get("status") == 200:
                self.contracts.add(Contract(name=path, method=method, path=path, level="minimal"))
        # 计数用 registry 净增，不用条目数：真实抓包同路径多次出现（轮询接口），add 按
        # (method,path) 覆盖，逐条计数会虚报登记数，与 registry 实际大小漂移。
        return len(self.contracts.contracts) - before

    def export_jmx(self, **filters) -> Any:
        return self.bridge.call("export_jmx", filters)

    def export_har(self, **filters) -> Any:
        return self.bridge.call("export_har", filters)

    def export_python(self, **filters) -> Any:
        return self.bridge.call("export_python", filters)

    def console_errors(self, limit: int = 100) -> list:
        """只取 error 级控制台输出，供失败调查；不替代后端响应实证。

        真实 bridge 取证（v1.1.4）：`console_read` 在部分 build 返回**纯文本**
        「错误: 未知方法: console.read」。旧实现 `result.get("items", result)` 再
        `isinstance(items, list) else []`——把错误文本、无 items 的错误 dict、None
        全部折叠成 `[]`：调查者读成「前端无错误」，实为感知器官坏了的**假阴性**
        （#102 静默登记 0 条、#104 假 .har 坏档同族——坏形态伪装成合法空结果）。
        合法空表 `{"items": []}` / 裸 `[]` 仍如实返回 []（那是业务事实：本轮无报错）；
        非 dict 非 list 的形态与两键皆无的 dict 一律点名拒绝，不猜不折叠。

        #120 面1：真实桥接 `console.read` 权威键名是 **entries**（bridge-client.js v1.1.4：
        `{total, filtered, entries}`，与 net_list 同族），不是离线 fake 的 items——旧实现
        只认 items，真实抓到的错误被点名「返回 dict 但无 items 列表」当场拒绝：把正常路径
        翻成错误（#115 教训的反向面）。现 entries 优先、items 兼容旧 fake，键名收敛单一口径
        （与 learn_contracts 的 entries 优先/items 兼容同纪律）。
        """
        # #140：解包判据收敛 `_unwrap_records` 单一真相——与 learn_contracts 共用，
        # 消息口径逐字不变（source="console_read"），杜绝两函数各自演化后防线强度漂移。
        result = self.bridge.call("console_read", {"level": "error", "limit": limit})
        return _unwrap_records(result, "console_read")

    def save_har(self, path: Path, **filters) -> Path:
        """导出抓包前必须确认不含生产凭据；本方法不做脱敏，由使用者负责。

        真实 web-toolbox 的 export_har 返回形态有三种（v1.1.4 bridge 实测）：
        ① 有匹配条目→HAR 对象 {log:{...}}；② 过滤后无匹配→纯文本「错误: 过滤后没有条目」
        （不是 dict）；③ 工具文档口径「返回 JSON 文本」（str）。旧实现无条件
        json.dumps(结果)：②③ 会被二次编码——错误文本写成 `""错误: 没有条目""`、
        JSON 文本顶层变成字符串而非 {log:...}。文件「保存成功」实为坏档，下游
        DevTools/Postman 打开才炸，且真因（过滤没命中）被埋进引号里查不到（与 #102
        「模块存在但没接」同族：离线 fake 返回 dict 永远测不出真实桥接的非 dict 形态）。
        这里按真实形态分流：str 先试解析（合法 JSON→用解析对象；坏 JSON/错误文本→
        ValueError 点名原文并拒写坏档）；dict/list 正常序列化；None/其他类型发送前点名。
        """
        har = self.export_har(**filters)
        if isinstance(har, str):
            try:
                har = json.loads(har)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"export_har 返回的不是可解析的 HAR，拒绝写坏档 {path}"
                    f"（原文：{har[:200]!r}；过滤可能没命中任何条目）") from exc
        if har is None or not isinstance(har, (dict, list)):
            raise ValueError(
                f"export_har 返回了无法写为 HAR 的形态 {type(har).__name__}，拒写坏档：{path}")
        # #140 第二面：#104 只堵了 str 文本面，**错误 dict 后门**仍在——bridge 返回
        # {"status":"error","message":"过滤后没有条目"}（形态与 learn_contracts 面实证同源）
        # 类型检查全过、照常落盘，产出一个「保存成功」的坏 .har：真因被埋进字段里，
        # 下游 DevTools/Postman 打开才炸（#102 坏形态伪装合法结果同族）。
        # HAR 1.2 规范顶层必含 log，缺 log 的 dict 即异常应答，点名拒写。
        # list 形态保留放行：无实测证据它是坏形态，不得凭空翻转正常路径（#115）。
        if isinstance(har, dict) and "log" not in har:
            raise ValueError(
                f"export_har 返回缺 log 结构的 dict，拒写坏档 {path}"
                f"（疑似 bridge 错误应答：{str(har)[:200]!r}；过滤可能没命中任何条目）")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(har, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

