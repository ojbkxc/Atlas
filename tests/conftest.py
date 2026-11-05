"""测试环境隔离：Atlas 配置键（ATLAS_*/DEMO_*/ISERVICE_*）在会话内确定化。

泄漏取证（#134，2026-11-04）：`load_dotenv` 用 `os.environ.setdefault` 写**进程环境**
（dotenv 惯例语义，产品侧正确——用户 shell 显式导出的键优先于文件）。测试里两类路径
把真实配置灌进进程：
① `AtlasConfig.from_root()` 不带 root 时用 cwd——pytest 进程 cwd 是仓库根，一旦用户按
  `.env.example` 指引创建本机 `.env`（接真实项目的标准姿势），`ATLAS_LEGACY_WEB`/
  `ATLAS_ENV_PREFIX` 等立即污染全进程；
② `atlas doctor`/runtime 装配 `load_dotenv(config.legacy_project)` 加载旧项目根 `.env`，
  `ISERVICE_URL_SIT` 等真实业务键随 tmp legacy fixture 泄漏。
monkeypatch 只回滚它**显式 setenv** 的键，对这些 setdefault 灌入的未知键无能为力；
于是 `pytest -q` 的绿依赖「本机恰好没配 .env」——配好真实环境的那一刻 4 个顺序相关
测试必红（实锤：test_browser_session_does_not_require_base_url 读到 real.example、
dry-run 撞 iservice、run 预检撞 DemoApi 路径、doctor 报 atlas.env）。

修法：会话开始清掉全部 Atlas 配置前缀键（确定性基线，堵①的 cwd 污染与②的历史
泄漏）；每个测试结束恢复快照（堵测试间互相泄漏）。产品代码零改动——dotenv
setdefault 语义对真实 CLI 用户是正确的，错的只是测试共享进程环境。
"""
import os

import pytest

_ATLAS_PREFIXES = ("ATLAS_", "DEMO_", "ISERVICE_")


def _atlas_keys(environ):
    return [k for k in environ if k.startswith(_ATLAS_PREFIXES)]


@pytest.fixture(scope="session", autouse=True)
def _deterministic_config_baseline():
    """会话起点：剥掉任何从外部（本机 .env、用户 shell）混进来的 Atlas 配置键。"""
    saved = {k: os.environ[k] for k in _atlas_keys(os.environ)}
    for k in saved:
        del os.environ[k]
    yield
    os.environ.update(saved)


@pytest.fixture(autouse=True)
def _restore_env_after_test():
    """函数级：load_dotenv 的 setdefault 在测试内灌入的键，测试结束一律回滚，
    不再漂给下一个测试（顺序无关化）。"""
    snapshot = os.environ.copy()
    yield
    for key in _atlas_keys(os.environ):
        if key not in snapshot:
            del os.environ[key]
        elif os.environ[key] != snapshot[key]:
            os.environ[key] = snapshot[key]
