"""conftest 环境隔离器自身的行为钉死（#134 附带修复的防线）。

被钉的对象：tests/conftest.py 的会话级基线（清 ATLAS_*/DEMO_*/ISERVICE_* 前缀键）+
函数级快照恢复。缺陷原型（#134 实锤）：`load_dotenv` 用 `os.environ.setdefault` 写
进程环境——`from_root()` 无 root 时加载 cwd 真实 `.env`、doctor 装配加载 tmp legacy/.env，
键一旦灌入全进程可见；monkeypatch 只回滚它显式 setenv 的键，对 setdefault 灌入的未知键
无能为力。配好本机 `.env`（用户接真实项目的标准姿势）后 `pytest -q` 顺序相关必红 4 个。
隔离器若被将来某次改动悄悄删掉/改坏，这组测试以**测试间泄漏**形态当场变红。
"""
import os


def test_setdefault_leak_visible_within_own_test():
    """会话基线清过前缀键（本机哪怕配了真实 .env），setdefault 灌入的键在**本测试内**可见
    ——证明泄漏通道真实存在（fixture 不是靠「恰好没泄漏」蒙绿，而是测后恢复）。"""
    assert "ATLAS_LEAK_PROBE_X" not in os.environ      # 基线：会话起点已清干净
    os.environ.setdefault("ATLAS_LEAK_PROBE_X", "1")
    assert os.environ["ATLAS_LEAK_PROBE_X"] == "1"     # 本测试内泄漏确实发生
    # 篡改已有键同理（load_dotenv 不动已有键，但代码里直接赋值会漂）
    os.environ["ATLAS_ENV_PREFIX"] = "LEAKED_"


def test_leak_not_visible_in_next_test():
    """顺序依赖钉死：上一个测试灌入/篡改的键对本测试不可见——函数级快照恢复生效。
    若有人删了 conftest 的 autouse fixture，本测试变红（隔离器防线的回归锚点；
    锚点在「同文件内相邻顺序」，pytest 默认按定义序执行。单独 `-k` 跑本测试会假绿——
    锚点须随文件跑，与 #134 缺陷本体（顺序相关红）同构反向钉）。"""
    assert "ATLAS_LEAK_PROBE_X" not in os.environ
    # 会话级基线：前缀键在隔离环境下恒不存在（用户 shell/本机 .env 的真实值不得渗入测试）
    for k in os.environ:
        assert not k.startswith(("ATLAS_", "DEMO_", "ISERVICE_")), f"基线泄漏：{k}"
