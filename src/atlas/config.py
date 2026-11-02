"""只从环境/.env 取秘密；业务 YAML 不承担凭据与秘密配置。"""
from __future__ import annotations
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

ENV_URL_KEYS = {
    "sit": "DEMO_URL_SIT", "uat-eu": "DEMO_URL_UAT_EU",
    "uat-amer": "DEMO_URL_UAT_AMER", "uat-hk": "DEMO_URL_UAT_HK",
    "prod-eu": "DEMO_URL_PROD_EU", "prod-amer": "DEMO_URL_PROD_AMER",
    "prod-hk": "DEMO_URL_PROD_HK",
}

def load_dotenv(root: Path) -> None:
    path = root / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if value[:1] == value[-1:] and value[:1] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)

def _asset_path(root: Path, key: str, default: str) -> Path:
    value = Path(os.environ.get(key, default))
    return (root / value).resolve() if not value.is_absolute() else value.resolve()

@dataclass(frozen=True)
class AtlasConfig:
    root: Path
    env: str = "sit"
    mode: str = "run"
    headless: bool = True
    channel: str = "chrome"

    def __post_init__(self) -> None:
        if self.mode not in {"run", "debug", "heal"}:
            raise ValueError("mode 只支持 run/debug/heal")
        if self.env not in ENV_URL_KEYS:
            raise ValueError(f"未知环境 {self.env!r}，可选：{', '.join(ENV_URL_KEYS)}")

    @classmethod
    def from_root(cls, root: str | Path | None = None, **overrides) -> "AtlasConfig":
        project_root = Path(root or os.environ.get("ATLAS_ROOT", Path.cwd())).resolve()
        if not (project_root / ".atlas-root").exists() and "ATLAS_ROOT" not in os.environ:
            for candidate in (project_root, *project_root.parents):
                if (candidate / ".atlas-root").exists():
                    project_root = candidate
                    break
        load_dotenv(project_root)
        values = {
            "env": os.environ.get("ATLAS_ENV", "sit"),
            "mode": os.environ.get("ATLAS_MODE", "run"),
            "headless": os.environ.get("ATLAS_HEADED", "0") != "1",
            "channel": os.environ.get("ATLAS_BROWSER_CHANNEL", "chrome"),
        }
        values.update(overrides)
        return cls(root=project_root, **values)

    @property
    def base_url(self) -> str:
        return os.environ.get(ENV_URL_KEYS[self.env], "").rstrip("/")

    @property
    def is_production(self) -> bool:
        return self.env.startswith("prod-")

    @property
    def artifacts_dir(self) -> Path:
        return self.root / "artifacts"

    @property
    def reports_dir(self) -> Path:
        return self.root / "reports"

    @property
    def slow_mo(self) -> int:
        return 300 if self.mode == "debug" and not self.headless else 0

    def external_project(self, key: str, default: str) -> Path:
        return _asset_path(self.root, key, default)

    def credential(self, username: str) -> tuple[str, str]:
        safe = re.sub(r"[^A-Za-z0-9_]", "_", username).upper()
        key = f"DEMO_PWD_{safe}"
        password = os.environ.get(key)
        if password is None and username == os.environ.get("DEMO_USERNAME"):
            password = os.environ.get("DEMO_PASSWORD", "")
        if password is None and self.is_production and username == os.environ.get("DEMO_PROD_USERNAME"):
            password = os.environ.get("DEMO_PROD_PASSWORD", "")
        return key, password or ""

    def validate_write(self, remark: str = "") -> None:
        if self.is_production:
            raise PermissionError("生产环境禁止建单、保存、提交、删除等写操作")
        if remark and "非人工创建" not in remark:
            raise ValueError("自动创建业务单据的备注必须包含「非人工创建」")

    def validate_base_url(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("base_url 必须是有效的 HTTP(S) 地址")
