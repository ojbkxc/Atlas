"""通用知识包（`plugins/_common/knowledge/`）：跨平台共享的坑点/组件族/接入清单。

**这里只放目录标记，不放代码。** 存在的唯一目的是让 `atlas.plugins._common` 成为一个
真正的 package：否则 `[tool.setuptools.packages.find]` 不会收录它，`knowledge/*.json`
也就进不了 wheel——装完之后 `plugin_api.common_knowledge_root()` 指向一个不存在的目录，
两个内置插件的 `base_roots` 组合层静默失效（82 条通用坑点/15 个通用组件全部不可见）。

路径本身仍由 `plugin_api.common_knowledge_root()` 单点给出（#92：不在这里拼第二份）。
"""
