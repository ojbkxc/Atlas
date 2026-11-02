"""端到端验证 create → find 复用 → UI 验证的链路。

用**本地桩脚本**代替真实建单：SIT DEMO 目前没有已验证的建单脚本，
框架不能凭空猜接口。这里验证的是：
  1) ScriptedCreator 能把外部脚本接成 create
  2) create 结果必须带「非人工创建」与单号
  3) 同一需求第二次会走 find 复用，不再建单（造单 ≤ 1）
  4) 配额超限在调用脚本前就被拦截
真实建单只需把 stub 换成已验证脚本即可，无需改引擎。
"""
from __future__ import annotations
import json
import sys
import tempfile
from pathlib import Path

from atlas.config import AtlasConfig
from atlas.creators import make_scripted_creator, require_audit_tag
from atlas.failures import EnvBlocked
from atlas.probes import CallableProbe, DataProvider


def build_stub(path: Path) -> None:
    path.write_text(
        "import json, sys\n"
        "where = json.load(sys.stdin)\n"
        "print(json.dumps({'orderNo': 'STUB-0001',\n"
        "                  'remark': 'API造数验证非人工创建'}, ensure_ascii=False))\n",
        encoding="utf-8")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="atlas_create_"))
    stub = tmp / "stub_create.py"
    build_stub(stub)

    store: list[dict] = []

    def finder(where):
        # 模拟“复用已存在数据”：第一次没有，第二次能找到
        return store[0] if store else None

    creator = make_scripted_creator(stub)

    def create(where):
        result = require_audit_tag(creator(where))
        store.append(result)
        return result

    provider = DataProvider(AtlasConfig(tmp), [CallableProbe("SalesOrder", finder, create)],
                            create_quota=1)

    actions = []
    for _ in range(3):
        actions.append(provider.supply({"entity": "SalesOrder"}).action)

    print(json.dumps({"actions": actions, "created": len(provider.quota.created),
                      "stored": len(store), "documents": provider.quota.created},
                     ensure_ascii=False, indent=2))

    # 配额已用完，再要求 create 必须被拦（且不会再调用脚本）
    try:
        provider.supply({"entity": "SalesOrder", "strategy": "create"})
        print("FAIL: 配额未生效", file=sys.stderr)
        return 1
    except EnvBlocked:
        pass

    assert actions == ["create", "find", "find"], actions
    assert len(store) == 1
    print("create → find 复用链: PASSED（造单 1 次，后 2 次复用）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
