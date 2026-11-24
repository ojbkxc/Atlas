"""`devops-sync.yaml` **未知键**必须可见：拼错的键不许静默被吞。

## 挡的是什么（2026-11-24 实测）

`load_config` 旧实现只 `data.get(<key>)` 取值，**未知键静默忽略**。我把 MCP 地址写成嵌套的

```yaml
mcp:
  url: https://...      # ← 不是合法键
```

`atlas devops-sync` 照常 rc 0，输出里 `"mcp_url": ""`——直到 `--execute` 才报
「mcp.json 中发现多个 devops MCP 服务器」才发现根本没配 MCP。配置面静默吞键与
「静默丢弃」同族：错的是人的手，痛的是后来排障的人。

## 修法与边界

`_warn_unknown_config_keys`：未知键 → **一行 WARN 点名键与合法键全集**，**不阻断**
（rc 仍 0）。不阻断的理由：配置里常留注释性扩展键，直接 rc 2 会把能用配置翻成错误
（#115 不把正常路径翻成错误）；但静默更坏——拼错的键会让人以为配好了。

## 四条

1. `test_unknown_key_warns_with_legal_list`：未知键进 stderr、消息里同时有点名的键与合法键全集；
2. `test_unknown_key_does_not_fail`：**不阻断**——rc/返回值形态不变（正向对照，防「修过头」）；
3. `test_legal_config_is_silent`：全部合法键时**零 WARN**（正向对照：不能天天噪声）；
4. `test_key_list_locks_to_dataclass`：`_CONFIG_KEYS` 与 `DevopsSyncConfig` 字段**逐项相等**
   （加字段忘登记 → 合法键会被误报成未知；这条防漂移）。
"""
from __future__ import annotations

from dataclasses import fields

from atlas import devops_sync as ds

_BASE = {
    "system_id": "1001",
    "system_name": "DemoSystem",
    "exec_user": "tester",
}


def _write(tmp_path, extra: dict) -> "object":
    path = tmp_path / "devops-sync.yaml"
    lines = [f'{k}: "{v}"' for k, v in {**_BASE, **extra}.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_unknown_key_warns_with_legal_list(tmp_path, capsys):
    path = _write(tmp_path, {"mcp": "nested"})
    ds.load_config(tmp_path, path=path)
    err = capsys.readouterr().err
    assert "未知配置键" in err, f"未知键没被点名：{err!r}"
    assert "mcp" in err, err
    assert "mcp_server" in err, f"WARN 必须给出合法键全集（否则人还是不知道该怎么写）：{err!r}"


def test_unknown_key_does_not_fail(tmp_path, capsys):
    """正向对照：只是警告，配置仍可用（不能把能跑的配置翻成错误）。"""
    path = _write(tmp_path, {"mcp": "nested"})
    cfg = ds.load_config(tmp_path, path=path)
    assert cfg.system_id == "1001" and cfg.system_name == "DemoSystem"


def test_legal_config_is_silent(tmp_path, capsys):
    path = _write(tmp_path, {"testcase_directory": "DemoSystem/回归",
                             "mcp_server": "devops-mcp-sit"})
    ds.load_config(tmp_path, path=path)
    assert capsys.readouterr().err.strip() == "", "全部合法键时不该有 WARN（否则天天噪声）"


def test_key_list_locks_to_dataclass():
    declared = {f.name for f in fields(ds.DevopsSyncConfig)}
    assert set(ds._CONFIG_KEYS) == declared, (
        f"_CONFIG_KEYS 与 DevopsSyncConfig 字段不一致——多了或少了都会让合法键被误报：\n"
        f"  只在 _CONFIG_KEYS: {sorted(set(ds._CONFIG_KEYS) - declared)}\n"
        f"  只在 dataclass  : {sorted(declared - set(ds._CONFIG_KEYS))}")
