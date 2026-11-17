"""回落 demo 插件时，禁止拿虚构账号去连真实环境。

## 真机踩出来的形态（2026-11-16，实测而非推理）

真实项目（iservice / SIT）`.env` 里**没有** `ATLAS_PLUGIN` → `resolve_plugin()` 回落 demo
插件 → `session_defaults()` 给虚构账号 `demo_user`、`credential()` 拿**空密码** →
拿它去 `signinuat.midea.com` 登录 → 被拒 → 停在登录表单 → 45s 报「未见登录指示器」→ rc 1。

排障代价：我先怀疑 SSO 改版、再怀疑新增验证码，最后用一段只读探测脚本把
`resolve_account()` 打出来才发现是 `demo_user`。**报错信息里没有一个字提到「demo」。**

同进程的 `_warn_fallback_once()` 已经会把后果写进一行 WARN（en-1 升级过措辞），但它
**只警告不拦**，而一行 WARN 在几十行 stderr 里极易被忽略，代价是一次真实登录 + 一条
误导性超时。

## 判据（保守：只拦「必然失败」的那一格）

三条同时成立才拒绝：

1. 插件是 **回落** demo（`ATLAS_PLUGIN` 未设），不是显式 `ATLAS_PLUGIN=demo`；
2. 解析出的账号 == `DEMO_FIXTURE_ACCOUNT`（`demo_user`）；
3. 该账号的密码为空。

第 3 条是硬证据：有密码说明用户用 `ATLAS_SESSION_ACCOUNT` / `ISERVICE_PWD_*` 接管了
坐标，那就该放行——**不把正常路径翻成错误**（#115）。

## 为什么闸门放在 `precheck_session_inputs`

放在那一个序列里，`run` / `agent --execute` / 多 flow / `explore` / `explore --auto`
以及 `DemoRuntime.__enter__` 的 direct-use 全部自动获得该行为，不需要每个入口各写一遍
（那正是本仓被咬过多次的「改一漏一」）。
"""

from __future__ import annotations

import pytest

from atlas.config import AtlasConfig
from atlas.plugin_api import demo_fixture_account
from atlas.runtime import precheck_session_inputs, require_real_account_not_demo


def _project(tmp_path):
    """最小项目：旧仓 + DemoApi 目录都在（让其它预检项通过，只留本闸门作变量）。"""
    root = tmp_path / "proj"
    root.mkdir()
    (root / ".atlas-root").write_text("", encoding="utf-8")
    legacy = tmp_path / "legacy"
    (legacy / "legacy-web").mkdir(parents=True)
    api = tmp_path / "api"
    (api / "scripts").mkdir(parents=True)
    (api / "scripts" / "demo_api.py").write_text("", encoding="utf-8")
    return root, legacy, api


def _env(monkeypatch, legacy, api, *, plugin=None, account=None, password=None):
    monkeypatch.setenv("ATLAS_LEGACY_PROJECT", str(legacy))
    monkeypatch.setenv("ATLAS_DEMO_API_PROJECT", str(api))
    monkeypatch.setenv("DEMO_URL_SIT", "https://sit.example/")
    monkeypatch.setenv("ATLAS_ENV_PREFIX", "DEMO_")
    monkeypatch.delenv("ATLAS_PLUGIN", raising=False)
    monkeypatch.delenv("ATLAS_SESSION_ACCOUNT", raising=False)
    monkeypatch.delenv("DEMO_USERNAME", raising=False)
    monkeypatch.delenv("DEMO_PASSWORD", raising=False)
    monkeypatch.delenv("DEMO_PWD_DEMO_USER", raising=False)
    if plugin is not None:
        monkeypatch.setenv("ATLAS_PLUGIN", plugin)
    if account is not None:
        monkeypatch.setenv("ATLAS_SESSION_ACCOUNT", account)
    if password is not None:
        monkeypatch.setenv("DEMO_PASSWORD", password)


def test_demo_fallback_with_fixture_account_and_empty_password_is_rejected(tmp_path, monkeypatch):
    """核心：回落 demo + 虚构账号 + 空密码 → 预检拒绝，且消息点名「demo 账号」。

    这条是那个 20 分钟排障的直接回归钉。修复前这里会一路放行到 SSO 才超时。
    """
    root, legacy, api = _project(tmp_path)
    _env(monkeypatch, legacy, api)
    # 用真实主机——占位域会被第 4 条判据放过（见 test_gate_does_not_block_placeholder_hosts）
    monkeypatch.setenv("DEMO_URL_SIT", "https://icssit.midea.com/")
    config = AtlasConfig.from_root(root)

    with pytest.raises(ValueError) as ei:
        precheck_session_inputs(config, account=None, no_login=False, legacy_required=True)
    msg = str(ei.value)
    assert "demo" in msg.lower(), f"消息必须点名 demo（否则排障者看不到真因）：{msg}"
    assert demo_fixture_account() in msg, f"必须点名那个虚构账号：{msg}"
    # 可操作性：必须给出两条补救路径之一
    assert "ATLAS_PLUGIN" in msg, f"必须点名补救开关：{msg}"


def test_explicit_demo_plugin_is_not_blocked(tmp_path, monkeypatch):
    """反向对照：**显式** `ATLAS_PLUGIN=demo` 不拦（那是人的决定，样例/dry-run 合法）。

    这条防「把正常路径翻成错误」（#115）——显式声明还拒，就让所有离线样例跑不动。
    """
    root, legacy, api = _project(tmp_path)
    _env(monkeypatch, legacy, api, plugin="demo")
    config = AtlasConfig.from_root(root)
    # 不抛
    precheck_session_inputs(config, account=None, no_login=False, legacy_required=True)


def test_demo_fallback_with_real_password_is_not_blocked(tmp_path, monkeypatch):
    """反向对照：回落到 demo 但**密码已显式接管** → 放行。

    判据是「必然失败」而不是「名字像 demo」：有密码说明坐标是用户给的，该让他跑。
    密码键按 `credential()` 的实际查找链给：`{prefix}PWD_{ACCOUNT}` 优先，
    且仅当 account == `{prefix}USERNAME` 时回退到 `{prefix}PASSWORD`。
    """
    root, legacy, api = _project(tmp_path)
    _env(monkeypatch, legacy, api)
    # 走 `{prefix}PWD_{ACCOUNT}` 这条（与 account 是否等于 USERNAME 无关）
    monkeypatch.setenv("DEMO_PWD_DEMO_USER", "s3cret")
    config = AtlasConfig.from_root(root)
    # 不抛（account 仍是 demo_user，但密码非空 → 不满足「必然失败」）
    precheck_session_inputs(config, account=None, no_login=False, legacy_required=True)


def test_demo_fallback_with_username_fallback_password_is_not_blocked(tmp_path, monkeypatch):
    """反向对照 2：走 `{prefix}USERNAME` == account + `{prefix}PASSWORD` 那条回退链也放行。

    两个密码来源都要被认——只认一条会让「用 DEMO_USERNAME/DEMO_PASSWORD 接管」的
    正常路径被误拒（#115）。
    """
    root, legacy, api = _project(tmp_path)
    _env(monkeypatch, legacy, api)
    monkeypatch.setenv("DEMO_USERNAME", demo_fixture_account())
    monkeypatch.setenv("DEMO_PASSWORD", "s3cret")
    config = AtlasConfig.from_root(root)
    precheck_session_inputs(config, account=None, no_login=False, legacy_required=True)


def test_demo_fallback_with_explicit_account_is_not_blocked(tmp_path, monkeypatch):
    """反向对照：`--account` 显式给了**别的**账号 → 放行（不是虚构账号）。"""
    root, legacy, api = _project(tmp_path)
    _env(monkeypatch, legacy, api)
    config = AtlasConfig.from_root(root)
    # 不抛：account 不是 demo_user
    precheck_session_inputs(config, account="zhangfj73", no_login=False, legacy_required=True)


def test_no_login_skips_the_gate(tmp_path, monkeypatch):
    """`--no-login` 不登录 → 本闸门不适用（没有会话就没有「拿虚构账号登录」）。"""
    root, legacy, api = _project(tmp_path)
    _env(monkeypatch, legacy, api)
    config = AtlasConfig.from_root(root)
    # 不抛
    precheck_session_inputs(config, account=None, no_login=True, legacy_required=True)


def test_gate_is_reachable_directly_and_is_idempotent(tmp_path, monkeypatch):
    """闸门可被单独调用，且放行路径返回 None（不是靠异常以外的副作用）。"""
    root, legacy, api = _project(tmp_path)
    _env(monkeypatch, legacy, api, plugin="iservice")
    config = AtlasConfig.from_root(root)
    assert require_real_account_not_demo(config, demo_fixture_account()) is None


def test_message_names_the_env_prefix_for_the_password_hint(tmp_path, monkeypatch):
    """消息里的密码键前缀必须与实际 `env_prefix()` 一致（否则补救路径是错的）。

    这条防「消息写死 `ISERVICE_PWD_` 而平台前缀是 `DEMO_`」这类误导——用户照着做还是失败。
    """
    root, legacy, api = _project(tmp_path)
    _env(monkeypatch, legacy, api)
    monkeypatch.setenv("DEMO_URL_SIT", "https://icssit.midea.com/")
    config = AtlasConfig.from_root(root)
    with pytest.raises(ValueError) as ei:
        precheck_session_inputs(config, account=None, no_login=False, legacy_required=True)
    msg = str(ei.value)
    assert "DEMO_PWD_" in msg, f"消息里的前缀必须取自当前 env_prefix（DEMO_）：{msg}"
    assert "ISERVICE_PWD_" not in msg, f"不得写死别的平台前缀：{msg}"

# ---------------------------------------------------------------------------
# 4. 主机判据：只拦「真实主机」，保留域放过
# ---------------------------------------------------------------------------

def test_real_host_predicate_table():
    """`_looks_like_real_host` 的完整取值表（含反向：真实域必须判 True）。

    为什么需要这条：闸门若把 ``example.com`` 也当真实主机拦下来，所有离线套件
    （用占位域验证 rc/终态口径）都会红；反过来若把真实域判成占位，闸门就等于没有。
    两边都得钉。
    """
    from atlas.runtime import _looks_like_real_host

    cases = {
        # 真实主机 → 拦（True）
        "https://icssit.midea.com/": True,
        "https://signinuat.midea.com/login": True,
        "https://some-corp-portal.cn/": True,
        # 保留域 / 回环 / 内网 → 放过（False）
        "http://sit.example.com": False,
        "http://app.example.com": False,          # 子域也要覆盖，不只是裸域
        "http://example.com": False,
        "http://example.org": False,
        "https://foo.test/": False,
        "https://real.corp.invalid/": False,
        "http://localhost:8080": False,
        "http://127.0.0.1:1": False,
        "https://svc.local/": False,
        "http://10.1.2.3/": False,
        "http://192.168.1.10/": False,
        "http://172.20.0.5/": False,
        "": False,
        # 172.32 不在 RFC1918 段内 —— 必须是真实
        "http://172.32.0.1/": True,
    }
    bad = {u: _looks_like_real_host(u) for u, want in cases.items()
           if _looks_like_real_host(u) != want}
    assert not bad, f"主机判据与预期不符：{bad}"


def test_gate_does_not_block_placeholder_hosts(tmp_path, monkeypatch):
    """回落 demo + 空密码 + **占位域** → 不拦（离线套件正是这种组合）。

    这条是第一条的成对格：只拦真实主机，否则整套离线 rc/终态测试全红。
    """
    root, legacy, api = _project(tmp_path)
    _env(monkeypatch, legacy, api)
    config = AtlasConfig.from_root(root)
    # `_env` 设的是 https://sit.example.com/ —— 占位域，不满足第四条
    precheck_session_inputs(config, account=None, no_login=False, legacy_required=True)


def test_gate_blocks_real_host(tmp_path, monkeypatch):
    """回落 demo + 空密码 + **真实域** → 拦（真机踩到的那个形态）。"""
    root, legacy, api = _project(tmp_path)
    _env(monkeypatch, legacy, api)
    monkeypatch.setenv("DEMO_URL_SIT", "https://icssit.midea.com/")
    config = AtlasConfig.from_root(root)
    with pytest.raises(ValueError, match="demo"):
        precheck_session_inputs(config, account=None, no_login=False, legacy_required=True)
