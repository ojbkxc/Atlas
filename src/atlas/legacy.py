"""旧项目适配层：原文件原目录执行，不改其引擎、不复制业务代码。"""
from __future__ import annotations
import importlib.util
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from .config import AtlasConfig, load_dotenv
from .core import Channel, Task, TaskMetadata

@dataclass(frozen=True)
class LegacyCase:
    project_root: Path
    yaml_path: Path
    case_id: str

    @property
    def module_root(self) -> Path:
        return self.project_root / "legacy-web"

class LegacyAdapter:
    def __init__(self, config: AtlasConfig, project_root: Path | None = None):
        self.config = config
        self.root = (project_root or config.external_project("ATLAS_LEGACY_PROJECT", "../legacy-suite")).resolve()
        load_dotenv(self.root)

    def case(self, relative_yaml: str, case_id: str) -> LegacyCase:
        path = (self.root / relative_yaml).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("旧用例路径必须位于 legacy-suite 项目内")
        return LegacyCase(self.root, path, case_id)

    def pytest_command(self, node: str = "testcases/demo_sit_regression/test_demo_sit_regression.py",
                       headed: bool = False, keyword: str = "") -> list[str]:
        command = [sys.executable, "-m", "pytest", node, "-v", "-s",
                   "--headed" if headed else "--headless"]
        if keyword:
            command += ["-k", keyword]
        return command

    def run_pytest(self, node: str, headed: bool = False, keyword: str = "") -> int:
        import subprocess
        env = dict(os.environ)
        # Atlas 的 observability 不偷偷替换旧引擎，默认保持同事项目结果与速度。
        return subprocess.run(self.pytest_command(node, headed, keyword),
                              cwd=self.root / "legacy-web", env=env).returncode

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
        # 旧用例模块会 `from lib.pages import ...`，必须把旧项目 legacy-web/ 放进 sys.path。
        # 先放后放都会影响解析，这里插在最前面并保持幂等。
        for candidate in path.parents:
            if (candidate / "lib").is_dir() and (candidate / "conftest.py").exists():
                root = str(candidate)
                if root not in sys.path:
                    sys.path.insert(0, root)
                break
        spec = importlib.util.spec_from_file_location(f"atlas_legacy_{path.stem}_{number}", path)
        if not spec or not spec.loader:
            raise ImportError(str(path))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        fn = getattr(module, f"step_{number}", None)
        if fn is None:
            raise AttributeError(f"{path.name} 未实现 step_{number}")
        return cls(fn, f"{path.stem}.step_{number}")
