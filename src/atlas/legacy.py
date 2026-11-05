"""旧项目适配层：原文件原目录执行，不改其引擎、不复制业务代码。"""
from __future__ import annotations
import importlib.util
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from .config import AtlasConfig, check_legacy_paths, load_dotenv
from .core import Channel, Task, TaskMetadata

@dataclass(frozen=True)
class LegacyCase:
    project_root: Path
    yaml_path: Path
    case_id: str
    web_dir: str = "legacy-web"

    @property
    def module_root(self) -> Path:
        return self.project_root / self.web_dir

class LegacyAdapter:
    def __init__(self, config: AtlasConfig, project_root: Path | None = None):
        self.config = config
        self.root = (project_root or config.legacy_project).resolve()
        load_dotenv(self.root)

    def case(self, relative_yaml: str, case_id: str) -> LegacyCase:
        path = (self.root / relative_yaml).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("旧用例路径必须位于 legacy-suite 项目内")
        return LegacyCase(self.root, path, case_id, self.config.legacy_web)

    def pytest_command(self, node: str | None = None, headed: bool = False,
                       keyword: str = "") -> list[str]:
        command = [sys.executable, "-m", "pytest", node or self.config.legacy_test_node,
                   "-v", "-s", "--headed" if headed else "--headless"]
        if keyword:
            command += ["-k", keyword]
        return command

    @property
    def exec_dir(self) -> Path:
        """pytest 的工作目录——与 run_pytest 的 cwd、dry-run 展示同源，唯一真相。"""
        return self.root / self.config.legacy_web

    def check_exec_dir(self) -> None:
        """旧项目根或 web 子目录缺失时给可定位错误，不让 subprocess 抛 OS 级裸崩。

        真实漂移点：`ATLAS_LEGACY_PROJECT` 指错、`ATLAS_LEGACY_WEB` 与旧版目录名不符
        （legacy-web vs iservice）。修复前 subprocess.run(cwd=…) 直接抛
        `NotADirectoryError: [WinError 267] 目录名称无效。`——不含任何配置线索，
        用户看不出该改哪个键。显式终止优于让底层噪声冒充框架缺陷。

        #138：判据与消息委托 `config.check_legacy_paths` 单一真相——run 轨
        （runtime/CLI 预检）与 pytest 轨（这里）共用一套，杜绝两处复制后改一漏一漂移
        （#92/#97）。root 用 self.root（LegacyAdapter 构造时可显式传入覆盖
        config.legacy_project），行为与原两分支逐字等价，通过路径零涟漪（#115）。"""
        check_legacy_paths(self.root, self.config.legacy_web)

    def run_pytest(self, node: str | None = None, headed: bool = False, keyword: str = "") -> int:
        import subprocess
        self.check_exec_dir()
        env = dict(os.environ)
        # Atlas 的 observability 不偷偷替换旧引擎，默认保持同事项目结果与速度。
        return subprocess.run(self.pytest_command(node, headed, keyword),
                              cwd=self.exec_dir, env=env).returncode

class LegacyStep(Task):
    """将旧 step_N 包装成 Task；旧函数仍接收原生 page/ctx。"""
    def __init__(self, function: Callable, name: str, metadata: TaskMetadata | None = None):
        self.function = function
        self._name = name
        self.metadata = metadata or TaskMetadata(domain="legacy", intent=name,
                                                channel=Channel.UI, read_only=False)

    @property
    def name(self) -> str:
        return self._name

    def perform(self, actor) -> Any:
        return self.function(actor.page, actor.ctx)

    @classmethod
    def from_module(cls, path: Path, number: int) -> "LegacyStep":
        path = Path(path).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"旧步骤模块不存在：{path}")
        # 旧用例模块会 `from lib.pages import ...`，必须把旧项目 web 子目录
        # （默认 legacy-web，真实项目可能是 iservice）放进 sys.path。
        # 按「祖先目录含 lib/ 且含 conftest.py」动态探测，不依赖目录名。
        probe_root = None
        for candidate in path.parents:
            if (candidate / "lib").is_dir() and (candidate / "conftest.py").exists():
                probe_root = str(candidate)
                break
        if probe_root is not None and probe_root not in sys.path:
            sys.path.insert(0, probe_root)
        spec = importlib.util.spec_from_file_location(f"atlas_legacy_{path.stem}_{number}", path)
        if not spec or not spec.loader:
            raise ImportError(str(path))
        module = importlib.util.module_from_spec(spec)
        # #119 面A：探测零命中（probe_root=None）且模块 import lib.* 时，旧实现裸抛
        # ModuleNotFoundError: No module named 'lib'——用户分不清「探测没命中 lib/+conftest.py
        # 的祖先」还是「真缺 lib」，也无从知道该把模块放哪（真实缺陷①「No module named lib」
        # 的残留面：修了「放进 path」没修「探测失败归因」，#90 点名配置键同口径）。
        # 但**不在加载前拒绝**：不 import lib 的纯本地步骤模块在任意目录都合法（#115 教训：
        # 归因修复不得把正常路径翻成错误）。撞 ModuleNotFoundError 时按探测结果补归因再抛。
        try:
            spec.loader.exec_module(module)
        except ModuleNotFoundError as exc:
            if probe_root is not None or (exc.name or "").split(".")[0] != "lib":
                raise   # 探测命中却仍缺 lib=旧项目真缺；坏的是别的模块——原样传播不揽责
            raise ImportError(
                f"旧步骤模块 {path} 需要 `lib`，但向上未探测到含 lib/ 与 conftest.py 的"
                f"旧项目 web 根（检查 ATLAS_LEGACY_WEB，旧版可能是 legacy-web 或 iservice；"
                f"探测起点：{path.parent}）") from exc
        fn = getattr(module, f"step_{number}", None)
        if fn is None:
            raise AttributeError(f"{path.name} 未实现 step_{number}")
        # #119 面B：step_N 存在但不可调用（模块里 `step_3 = "..."` 字符串/None/类变量）——
        # 旧实现直接包装成功，perform 时才 TypeError「'str' object is not callable」：
        # 构造期假成功把错误推迟到真实执行链（#94 坏 creator 在建单前当场点名同口径）。
        if not callable(fn):
            raise TypeError(f"{path.name} 的 step_{number} 必须是可调用函数，"
                            f"实际是 {type(fn).__name__}")
        return cls(fn, f"{path.stem}.step_{number}")
