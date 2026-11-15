"""线性 DSL 编译为 Task 序列；数据条件和 API/UI 混用留在资产层。

`load()` 返回的 flow 带**源文本形态**（`FlowDocument`），编译期用它做三件事：
- **候选校验 → 原子应用**：任何一步不合法就整体拒绝，绝不把半成品 Task 列表交给执行；
- **md5 编译缓存**：同一 FlowCompiler 实例内源文本 md5 未变（且注册表代次未变）直接复用；
- **^ 指针报错**：未定义引用类报错带 `--> 文件:行:列` + 源行 + `^`（仿 LiteFlow
  `buildDataNotFoundExceptionMsg`），作者不用回头猜是哪一行写错。
"""
from __future__ import annotations
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
import yaml
from .core import Channel, FunctionalTask, Task, TaskMetadata
from .failures import (CONTROL_FLOW, EnvBlocked, AwaitingHuman, FailureClass,
                       ReconciliationMismatch, exception_brief, raise_type,
                       reconcile_diff)
from .probes import DataProvider, DataRequirement
from . import versioning
from . import failures   # en-12：exception_text / exception_message 走 failures 单一真相

# 步骤保留键：编译器消费后不透传给 Task 工厂。`requires` 是作者声明的依赖（编译期按
# 未定义引用校验，见 _check_produces）、`dispatch` 是路由声明（不透传，否则工厂收到
# 未知 kwargs 裸 TypeError）、`read_only`/`id`/`intent` 同族——LegacyCaseTask 的文档记过
# 「写了也不透传」的坑，这里集中一处，不再散落字面量集合。
# `raises`（en-12）：负向断言声明——期待本步抛出指定异常；编译器消费成 `_RaisesTask`
# 包装器，不透传（工厂收到未知 kwargs 会裸 TypeError）。
_RESERVED_KEYS = {"task", "channel", "intent", "requires", "read_only",
                  "on_env_blocked", "id", "dispatch",
                  # gap9b B-2 声明位（fast 导出消费；引擎路径不透传成 Task 参数）
                  "page_object", "legacy_pages",
                  # en-12 负向断言（期待抛）
                  "raises"}
# flow 级保留键：`template`/`slots` 是骨架实例化声明（load 期文本替换掉，不参与编译）。
_TEMPLATE_KEYS = {"template", "slots"}
# 骨架占位符 `{{name}}`：name 限标识符字符，嵌套花括号不当占位符吞掉。
_SLOT = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_.-]*)\s*\}\}")
# 骨架引骨架的深度上限：循环引用靠 visited 真实路径挡，超深靠本上限挡（不无界递归）。
_TEMPLATE_DEPTH = 8
# 进程内编译缓存条目上限（有界：长驻批量回归不因缓存无界涨内存）。
_CACHE_LIMIT = 64

_PATTERN = re.compile(r"\$\{([^}]+)\}")
_ASSERT_EXPR = re.compile(r"^(.*?)\s*(==|!=|>=|<=|>|<)\s*(.*)$")
# 路径形态操作数：字母/下划线开头且至少一个点（source_order.ptx）。
# "3.14" 数字开头不算、"NET30" 无点不算——都是合法字面量。
_PATH_OPERAND = re.compile(r"[A-Za-z_]\w*(?:\.\w+)+")
_ASSERT_OPS = {"==": lambda a, b: a == b, "!=": lambda a, b: a != b,
               ">=": lambda a, b: a >= b, "<=": lambda a, b: a <= b,
               ">": lambda a, b: a > b, "<": lambda a, b: a < b}

def interpolate(value: Any, ctx: dict) -> Any:
    if isinstance(value, dict):
        return {key: interpolate(val, ctx) for key, val in value.items()}
    if isinstance(value, list):
        return [interpolate(item, ctx) for item in value]
    if not isinstance(value, str):
        return value
    match = _PATTERN.fullmatch(value)
    if match:
        return _lookup(ctx, match.group(1))
    return _PATTERN.sub(lambda m: str(_lookup(ctx, m.group(1))), value)

def _lookup(ctx: dict, path: str) -> Any:
    """按 '.' 逐段解析 ctx 路径。失败仍抛 KeyError：
    _resolve_operand 依赖 KeyError/AttributeError/TypeError 做「路径→字面量」回退，
    类型不能改。统一成带路径语境的 KeyError，使大型/导入 flow 里写错的插值表达式
    可归因——指出整条路径与卡住的段（裸 KeyError('pt') 在多步 flow 里无法定位是哪条）。"""
    value: Any = ctx
    for index, key in enumerate(path.split(".")):
        try:
            value = value[key] if isinstance(value, dict) else getattr(value, key)
        except (KeyError, AttributeError, TypeError) as exc:
            raise KeyError(
                f"插值路径 '{path}' 在第 {index + 1} 段 '{key}' 处无法解析：{exc}") from exc
    return value

class FlowValidationError(ValueError):
    """flow 编译拒绝（全有或全无里的「全否」）：整个 flow 不产出可执行 Task 列表。

    继承 ValueError 与既有口径一致——`atlas compile/run` 的用户输入错误（rc 2）、generator
    的 ChecklistError、review_code 的结构自检都按 ValueError 捕获。
    """


class FlowReferenceError(FlowValidationError, KeyError):
    """未定义引用（Task 名 / 步骤 id / 数据落点 / 槽位不存在），消息带 ^ 指针定位。

    同时是 KeyError：`TaskRegistry.create` 与 assert 块的引用错误历史上就是 KeyError，
    调用方与既有回归测按 KeyError 捕获（`pytest.raises(KeyError, match="未注册")`）——
    把它翻成纯 ValueError 是无谓的行为变更（红线 #115：归因修复不得把正常路径翻成错误）。

    `__str__` 必须覆盖：`KeyError.__str__` 返回 args[0] 的 **repr**，多行 ^ 指针消息会被
    转义成字面 `\\n` 并裹上引号，CLI 那一行 stderr 糊成读不出来的乱码。
    """

    def __str__(self) -> str:
        return str(self.args[0]) if self.args else ""


def _walk_marks(node, path: tuple, out: dict) -> None:
    """把 `yaml.compose` 的节点树摊平成 `路径元组 -> (行, 列, 宽度)`（均 1 起）。"""
    start, end = node.start_mark, node.end_mark
    # 块形态值（映射/列表）跨行：^ 只打一格指到起始列，按 end-start 铺满会脱靶
    width = max(1, end.column - start.column) if start.line == end.line else 1
    out[path] = (start.line + 1, start.column + 1, width)
    if isinstance(node, yaml.MappingNode):
        for key_node, value_node in node.value:
            if isinstance(key_node, yaml.ScalarNode):
                name = str(key_node.value)
                _walk_marks(value_node, path + (name,), out)
    elif isinstance(node, yaml.SequenceNode):
        for index, item in enumerate(node.value):
            _walk_marks(item, path + (index,), out)


class FlowDocument(dict):
    """`load()` 的返回值：flow 映射 + **源文本形态**（md5 缓存键 + 节点定位表）。

    内存里手搓的普通 dict 没有源文本，两件事自动降级：编译缓存不命中（每次照构造，与
    改动前行为一致）、报错退化成「步骤 N」粗定位。降级不是静默兜底——消息照样点名步骤
    序号、键名与实际收到的值，只是给不出行号（拿不到源文本就不猜行号）。
    """

    def __init__(self, data=None, *, source_text: str = "", source_name: str = "<inline>"):
        super().__init__(data or {})
        self.source_text = source_text
        self.source_name = source_name
        self.md5 = hashlib.md5(source_text.encode("utf-8")).hexdigest() if source_text else ""
        self._marks: dict | None = None
        if source_text:
            try:
                root = yaml.compose(source_text, Loader=yaml.SafeLoader)
            except yaml.YAMLError:
                root = None   # 坏 YAML 由 safe_load 报正式错，这里只是拿不到定位表
            if root is not None:
                marks: dict = {}
                _walk_marks(root, (), marks)
                self._marks = marks

    def overlay(self, **extra) -> "FlowDocument":
        """带额外键的副本，**保留源文本形态**（CLI run 注入 engine 配置用）。

        `{**flow}` 会退化成普通 dict：md5 缓存与 ^ 定位一起丢——同一份 flow 白编译第二遍、
        登录后的结构报错白缺行号。注入配置必须走这里。
        """
        merged = FlowDocument({**self, **extra}, source_text=self.source_text,
                              source_name=self.source_name)
        merged._marks = self._marks
        return merged

    def pointer(self, path: tuple, message: str) -> str:
        """仿 LiteFlow 的未找到报错形态：消息 + `--> 文件:行:列` + 源行 + ^ 指示。"""
        location = self._marks.get(tuple(path)) if self._marks else None
        if location is None:
            return f"{message}\n  --> {self.source_name}（该处无法定位行列，行号不猜）"
        line, column, width = location
        return _pointer(message, self.source_text, self.source_name, line, column, width)


def _pointer(message: str, text: str, name: str, line: int, column: int,
             width: int = 1) -> str:
    gutter = str(line)
    lines = text.splitlines()
    source = lines[line - 1] if 0 < line <= len(lines) else ""
    return (f"{message}\n  --> {name}:{line}:{column}\n"
            f"  {gutter} | {source}\n"
            f"  {' ' * len(gutter)} | {' ' * (column - 1)}{'^' * max(1, width)}")


def _document(flow: Any) -> FlowDocument | None:
    """flow 是否带源文本形态（文件加载来的都有，内存 dict 没有）。"""
    return flow if isinstance(flow, FlowDocument) and flow.source_text else None


def _slot_error(text: str, name: str) -> FlowValidationError | None:
    """源文本里残留的 `{{slot}}` → 带 ^ 的未填槽位错误；没有残留返回 None。

    骨架（abstract chain）本身不是可执行 flow：槽位没填完就编译，等于把字面量
    `{{customer}}` 当客户号发给系统（假数据假通过），所以必须在编译期拦住。
    """
    found = [(number, match.start(1), match.group(1))
             for number, line in enumerate(text.splitlines(), 1)
             for match in _SLOT.finditer(line)]
    if not found:
        return None
    names = sorted({item[2] for item in found})
    line, column, _ = found[0]
    message = (f"模板槽位未填全：{names}——骨架 flow 必须经 template+slots 实例化后才能编译"
               f"（未填的槽位不是「空值」，是没决定）")
    return FlowValidationError(_pointer(message, text, name, line, column + 1))


class TaskRegistry:
    def __init__(self):
        self._tasks: dict[str, Callable[..., Task]] = {}
        # 注册表代次：每次 register 递增。缓存键只有 md5 不够——注册表变了（补注册/换工厂）
        # 而文本没变时，旧缓存会把「现在才合法」的 flow 继续拒掉，或放行旧工厂造的实例。
        self.generation = 0

    def register(self, name: str, factory: Callable[..., Task]) -> None:
        self._tasks[name] = factory
        self.generation += 1

    def known(self, name: str) -> bool:
        """候选校验期只问「注册没注册」，不构造 Task（校验阶段保持零副作用）。"""
        return name in self._tasks

    def names(self) -> list[str]:
        """报错时列出真实可用清单——「不猜接口」的另一面是让作者照抄真名。"""
        return sorted(self._tasks)

    def create(self, name: str, **kwargs) -> Task:
        if name not in self._tasks:
            raise KeyError(f"Task 未注册：{name}")
        return self._tasks[name](**kwargs)


@dataclass
class _Router:
    """dispatch 的编译产物：读 `on` 字段 → 按取值选分支，default 必选（无隐式通道）。

    lanes 的键统一存字符串（YAML 键 `1003`/`true` 与 ctx 里的 int/bool 靠 str() 对齐），
    值恒为非空字符串列表。`gate` 是本路由的标识（步骤 id，未声明时用 `#序号`）：运行期把
    选中的分支 id 写进 `ctx["_dispatch_selected"][gate]`，由 `FlowCompiler.run()` 过滤未选中分支。
    """
    gate: str
    on: str
    lanes: dict[str, list[str]] = field(default_factory=dict)
    default: list[str] = field(default_factory=list)

    def select(self, value: Any) -> list[str]:
        """按 ctx 值选分支：精确匹配 → str() 匹配 → default（default 编译期强制存在）。"""
        if value in self.lanes:
            return self.lanes[value]
        text = str(value)
        if text in self.lanes:
            return self.lanes[text]
        return self.default


def _dispatch_map(raw: dict) -> tuple[dict, list[str]]:
    """把 dispatch 映射的键归一成字符串，并返回归一化过程中的问题清单。

    YAML 1.1 把不带引号的 `on/off/y/yes/no/true/false` 解析成**布尔值**——`dispatch.on:`
    正是最自然的写法（#121 的 `step: yes` 同一族坑，那处是拒绝，这里不能拒：拒了等于
    让作者必须写成 `'on':` 才能用，而这个键名是规格定的）。所以 True→"on" 归一；
    False→"off" 落到未知字段报错里点名（不猜作者想写什么），两个键归一撞车也点名。
    """
    normalized: dict[str, Any] = {}
    problems: list[str] = []
    for key, value in raw.items():
        name = "on" if key is True else ("off" if key is False else str(key))
        if name in normalized:
            problems.append(f"dispatch 的键 {name!r} 重复（YAML 1.1 把 on 与 'on' 看成两个键）")
            continue
        normalized[name] = value
    return normalized, problems


class FlowCompiler:
    def __init__(self, registry: TaskRegistry, provider: DataProvider | None = None):
        self.registry, self.provider = registry, provider
        # 进程内编译缓存：编译指纹（源文本 md5 + steps 结构指纹 + 注册表代次）-> Task 列表。
        # 为什么要它：一次 `atlas run` 至少编译两遍（登录前 validate_flow、run 内再一遍），
        # 批量回归还会对同一文件反复 load+compile，而工厂构造（legacy 模块加载、建单脚本包装）
        # 不便宜。只存内存：Task 实例带工厂闭包，落盘等于制造「磁盘产物 ≠ 当前代码」的脏状态。
        self._cache: dict[str, tuple[int, list[Task]]] = {}

    def load(self, path: str | Path) -> dict:
        try:
            text = Path(path).read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            # wave4 rb-5：#112 同族——flow.yaml 腰斩（写一半被中断）留非法 UTF-8 尾字节时，
            # 旧实现 read_text 裸 UnicodeDecodeError 直冒（cli 捕到它⊂ValueError→rc2，但消息
            # 是原始解码错、不点名文件）。与 JSONL 日志侧「replace 回落」不同：日志对付
            # 「部分证据仍有用」，flow 是**要执行的真相**，悄悄 replace 修好=跑一份被篡改的
            # 流程，比报错更坏（红线 #12 不猜）。点名文件与字节位，好文件逐字节不变。
            raise ValueError(
                f"flow 文件 {path} 读取失败：非法 UTF-8 字节（文件可能被截断/写坏），"
                f"位置 byte {exc.start}。请检查文件完整性后重试") from exc
        raw = self._parse(text, str(path))
        source_name = str(path)
        # 骨架实例化：`{template, slots}` → 文本级 {{slot}} 替换 → 替换后的文本才是 flow
        if isinstance(raw, dict) and "template" in raw:
            text, raw, source_name = self._instantiate(path, raw)
        if not isinstance(raw, dict) or not isinstance(raw.get("steps"), list):
            if isinstance(raw, dict) and "initial" in raw and "states" in raw:
                raise ValueError(f"{path} 是状态机文件（initial/states），请用 atlas machine 执行")
            raise ValueError(f"{path} 是 DSL flow，至少需要 steps 列表")
        return FlowDocument(raw, source_text=text, source_name=source_name)

    @staticmethod
    def _parse(text: str, name: str | Path):
        """解析 YAML；语法错先分清是不是「骨架没填槽位」这一因。

        `code: {{customer}}` 这种裸占位符在 YAML 里本身就是 flow-mapping 起始符，
        safe_load 报的是 `while constructing a mapping`——那是**未填槽位的下游症状**，
        不是作者的错因。原样抛出等于让人对着语法报错猜哪一行没填，所以这里先扫
        `{{name}}`，扫到就给带 ^ 的槽位报错（扫不到才是真语法错）。
        """
        try:
            return yaml.safe_load(text)
        except yaml.YAMLError as exc:
            error = _slot_error(text, str(name))
            if error is not None:
                raise error from exc
            raise

    @staticmethod
    def _slot_problem(text: str, skeleton: Path, declaring: Path) -> None:
        error = _slot_error(text, str(skeleton))
        if error is not None:
            raise FlowValidationError(
                f"{error}\n  （由 {declaring} 实例化：slots 少填了上面点名的槽位）")

    def _instantiate(self, path: str | Path, raw: dict, _depth: int = 1,
                     _seen: tuple[str, ...] = ()) -> tuple[str, dict, str]:
        """骨架 → 实例：文本级 `{{slot}}` 替换（仿 LiteFlow replaceAbstractChain 的 abstract 语义）。

        实例文件只能带 `template` + `slots`，其余键（steps/requires/assert/…）都在骨架里——
        多带即拒绝，不静默忽略（骨架与实例各持一半流程，出事时没人知道真相在哪份文件）。
        槽位值必须是**单行标量**：容器或多行字符串会让替换后的文本行数与骨架漂移，
        ^ 指针报出来的行号就指向不相干的行（宁可拒收，也不给错行号）。
        替换后仍残留 `{{...}}` = 骨架没填完，编译期拒绝产出可执行 flow（见 _check_slots）。
        """
        declaring = Path(path)
        unknown = sorted(key for key in raw if key not in _TEMPLATE_KEYS)
        if unknown:
            raise FlowValidationError(
                f"{declaring} 带 template 时只能再带 slots，多出：{unknown}——流程与数据供给"
                f"都写在骨架里，两份真相迟早漂移")
        slots = raw.get("slots") or {}
        if not isinstance(slots, dict):
            raise FlowValidationError(f"{declaring} 的 slots 必须是映射，收到 "
                                      f"{type(slots).__name__}：{slots!r}")
        name = str(raw.get("template") or "").strip()
        if not name:
            raise FlowValidationError(f"{declaring} 的 template 必须是非空骨架路径，收到 "
                                      f"{raw.get('template')!r}")
        skeleton = Path(name)
        if not skeleton.is_absolute():
            skeleton = (declaring.parent / name).resolve()
        if str(skeleton) in _seen:
            raise FlowValidationError(f"骨架循环引用：{skeleton} 已在链上 {list(_seen)}")
        if _depth > _TEMPLATE_DEPTH:
            raise FlowValidationError(f"骨架嵌套超过 {_TEMPLATE_DEPTH} 层（{skeleton}），拒绝")
        if not skeleton.exists():
            raise FlowValidationError(f"骨架文件不存在：{skeleton}（{declaring} 的 template）")
        text = skeleton.read_text(encoding="utf-8")
        for key, value in slots.items():
            if isinstance(value, (dict, list, tuple, set)) or value is None or "\n" in str(value):
                raise FlowValidationError(
                    f"{declaring} 的 slot {key!r} 必须是单行标量，收到 {type(value).__name__}："
                    f"{value!r}——容器/多行值会让骨架行号漂移，^ 指针报错就变成指错行")
            pattern = re.compile(r"\{\{\s*" + re.escape(str(key)) + r"\s*\}\}")
            text = pattern.sub(lambda _m, replacement=str(value): replacement, text)
        # 替换后还有 {{...}} = 骨架没填完：先点名槽位（带骨架行号），别让 YAML 语法错抢话
        self._slot_problem(text, skeleton, declaring)
        parsed = self._parse(text, skeleton)
        # 骨架自己也可以引骨架：递归到普通 flow（深度与环都有硬上限）
        if isinstance(parsed, dict) and "template" in parsed:
            return self._instantiate(skeleton, parsed, _depth + 1, (*_seen, str(declaring)))
        if not isinstance(parsed, dict) or not isinstance(parsed.get("steps"), list):
            raise FlowValidationError(f"骨架 {skeleton} 实例化后仍不是合法 flow（缺 steps 列表）")
        # 源文本名指骨架：^ 指针的行号是骨架的行号，括号里带上是哪个实例填的
        return text, parsed, f"{skeleton}（{declaring} 的骨架）"

    def compile(self, flow: dict) -> list[Task]:
        """候选校验 → 原子应用：任一步/引用不合法就整体拒绝，绝不产出半成品 Task 列表。

        分三段。**步骤 Task 在静态校验阶段一个都不构造**（工厂可能有真实副作用：legacy
        模块加载、建单脚本包装——半途构造到第 3 步才炸，前两步的对象就留在世上没人负责）：
          1. 静态校验：形态、task 名注册、保留键值域、步骤 id 唯一、dispatch 形态与目标集、
             模板未填槽位、assert 块——错误消息带 ^ 指针；
          2. 编译缓存命中则直接复用，否则构造候选列表（失败即丢弃、不进缓存）；
          3. 语义校验：步骤级 requires 与 dispatch.on 引用的 ctx 键必须有声明来源
             （flow 级 requires 落点 / 上游 Task 的 produces / 上游 save_as）。

        唯一提前构造的地方是 assert 块的**参数**校验——参数合不合法只有工厂自己说得清，
        产物用完即弃，且发生在任何步骤被真正装配之前。

        缓存在静态校验**之后**才查、语义校验每次都跑，键也不止 md5：md5 是**源文本**的指纹，
        拿到的 flow 却是可变 dict——load 之后被脚本改过一步（拼接/覆盖 flow 的常见写法）时，
        只认 md5 就会把旧编译结果盖在新内容上放行（改了 `dispatch.on` 却仍按老路由跑，
        是比慢一百倍更糟的代价）。所以键 = 源文本 md5 + **当前 steps 结构指纹** + 注册表代次；
        校验本身是纯判断，重跑比重造 Task（legacy 模块加载、建单脚本包装）便宜得多，省不得。
        """
        problems = self._check_static(flow)
        if problems:
            raise _combine(problems)
        digest = _fingerprint(flow, self.registry.generation)
        cached = self._cache[digest][1] if digest is not None and digest in self._cache else None
        tasks = cached if cached is not None else self._build(flow)
        late = self._check_produces(flow, tasks)
        if late:
            # 被拒的编译结果一律不进缓存：缓存里只留「整体通过过」的列表，
            # 否则半成品会在下一次同样内容的编译里借尸还魂
            raise _combine(late)
        if cached is None and digest is not None:      # 没有源文本的 flow 不进缓存（见 _fingerprint）
            self._cache[digest] = (self.registry.generation, list(tasks))
            while len(self._cache) > _CACHE_LIMIT:
                self._cache.pop(next(iter(self._cache)))
        return list(tasks)

    def _build(self, flow: dict) -> list[Task]:
        """按已通过静态校验的 steps 构造 Task（channel 覆盖、环境守卫、路由节点在此落地）。

        调用方（compile）保证校验已过；本函数抛错时列表不外泄、不进缓存（原子性）。
        包装顺序 channel → guard → router：外层包内层，插值统一由最外层的
        `_perform_interpolated` 触发，包装器的 `perform` 再往下透传（见 `_ChannelTask`）。
        """
        tasks: list[Task] = []
        for index, step in enumerate(_steps_of(flow), 1):
            task = self._create(flow, step, index)
            if "channel" in step:
                task = _ChannelTask(task, step)
            if step.get("on_env_blocked"):
                task = _GuardedTask(task, step["on_env_blocked"])
            router = _router_of(step, index)
            if router is not None:
                task = _RouterTask(task, router)
            if "raises" in step:
                # en-12：负向断言包装在最外层——异常穿过 channel/guard/router 时若被降级/
                # 跳过，就不该被 raises 接住（那条路径不是「抛了期望异常」，是「没跑到」）。
                task = _RaisesTask(task, step["raises"], index)
            tasks.append(task)
        return tasks

    def _create(self, flow: dict, step: dict, index: int) -> Task:
        """构造一步的 Task；工厂自己的报错（缺参/参数形态不对）点名是哪一步、哪个 Task。

        dispatch-only 步骤也走这里：`task` 缺失时构造一个 `_NoopTask`（纯路由节点，
        没有业务动作，只负责选中分支）。
        """
        name = step.get("task")
        if name is None:
            return _NoopTask(gate=_gate_of(step, index))
        args = {key: value for key, value in step.items() if key not in _RESERVED_KEYS}
        try:
            return self.registry.create(name, **args)
        except (TypeError, ValueError) as exc:
            # 「got an unexpected keyword argument」这类裸消息在多步 flow 里无从归因
            raise type(exc)(self._error_message(
                flow, ("steps", index - 1), f"步骤 {index} Task {name!r} 构造失败：{exc}")) from exc

    # ---- 候选校验（零构造） --------------------------------------------------------
    def _check_static(self, flow: dict) -> list[FlowValidationError]:
        """返回问题清单（每项都是带定位的异常对象）；空列表 = 放行。"""
        problems: list[FlowValidationError] = []
        problems.extend(self._check_slots(flow))
        policy = flow.get("on_env_blocked", "stop")
        if policy not in {"skip", "stop"}:
            problems.append(self._error(flow, ("on_env_blocked",),
                          f"flow 级 on_env_blocked 只支持 skip/stop，得到 {policy!r}"))
        # 落库线：flow 级 harvest 开关（跑绿后固化动作序列进审核塔）只收布尔——
        # 字符串 "yes"/数字 1 这类真值会让「声明了固化」与「没声明」的分界变模糊，
        # 与 flow 级 on_env_blocked 的严格值域同纪律（不猜作者想表达什么）。
        harvest = flow.get("harvest", False)
        if not isinstance(harvest, bool):
            problems.append(self._error(flow, ("harvest",),
                          f"flow 级 harvest 只支持布尔 true/false（跑绿后把动作序列固化为 "
                          f"flow_candidate 草稿进审核塔），得到 {harvest!r}"))
        problems.extend(self._check_await(flow))
        steps = flow.get("steps")
        if not isinstance(steps, list):
            problems.append(self._error(flow, (), "DSL 至少需要 steps 列表"))
            return problems
        declared: dict[str, int] = {}       # 步骤 id -> 序号（dispatch 目标集的唯一真相）
        for index, step in enumerate(steps, 1):
            if not isinstance(step, dict):
                problems.append(self._error(flow, ("steps", index - 1),
                              f"步骤 {index} 必须是映射，收到 {type(step).__name__}：{step!r}"))
                continue
            name = step.get("task")
            if name is None and "dispatch" not in step:
                problems.append(self._error(flow, ("steps", index - 1),
                              f"步骤 {index} 缺少 task（只有 dispatch 的纯路由步骤可省略）"))
            elif name is not None and (not isinstance(name, str) or not name.strip()):
                problems.append(self._error(flow, ("steps", index - 1, "task"),
                              f"步骤 {index} 的 task 必须是非空字符串，收到 {name!r}"))
            elif name is not None and not self.registry.known(name):
                problems.append(self._error(flow, ("steps", index - 1, "task"),
                              f"步骤 {index} 引用未注册的 Task：{name!r}"
                              f"（registry 现有：{self.registry.names()}）"))
            problems.extend(self._check_step_id(flow, step, index, declared))
            if "channel" in step and str(step["channel"]) not in {c.value for c in Channel}:
                problems.append(self._error(flow, ("steps", index - 1, "channel"),
                              f"步骤 {index} channel 只支持 ui/api/auto，收到 {step['channel']!r}"))
            if "on_env_blocked" in step and step["on_env_blocked"] not in {"skip", "stop"}:
                problems.append(self._error(flow, ("steps", index - 1, "on_env_blocked"),
                              f"步骤 {index} on_env_blocked 只支持 skip/stop，"
                              f"收到 {step['on_env_blocked']!r}"))
            if "requires" in step and not _requires_shape_ok(step["requires"]):
                problems.append(self._error(flow, ("steps", index - 1, "requires"),
                              f"步骤 {index} 的 requires 必须是非空字符串或非空字符串列表，"
                              f"收到 {step['requires']!r}"))
            problems.extend(self._check_page_decl(flow, step, index))
            problems.extend(self._check_raises(flow, step, index))
        problems.extend(self._check_dispatch(flow, steps, declared))
        problems.extend(self._check_asserts(flow))
        return problems

    def _check_raises(self, flow: dict, step: dict,
                      index: int) -> list[FlowValidationError]:
        """en-12 负向断言**形态**判据（compile 静态段单点，run/export-pytest 自动同尺）。

        `raises:` 必须是映射，`type` 必填且经 `failures.raise_type` 解析成**内置异常类**
        （解析失败=编译拒绝，坏名不进运行期）；可选 `match` 是正则字符串（对异常 str 形态
        做 `re.search`）。坏形态**整体拒绝**、一个 Task 都不构造（原子应用纪律）。
        键缺席 = 无声明，存量 flow 零涟漪。"""
        problems: list[FlowValidationError] = []
        if "raises" not in step:
            return problems
        raw = step["raises"]
        if not isinstance(raw, dict):
            return [self._error(flow, ("steps", index - 1, "raises"),
                    f"步骤 {index} 的 raises 必须是映射（{{type, match?}}），收到 "
                    f"{type(raw).__name__}：{raw!r}")]
        unknown = sorted(k for k in raw if k not in {"type", "match"})
        if unknown:
            problems.append(self._error(flow, ("steps", index - 1, "raises"),
                              f"步骤 {index} raises 未知字段：{unknown}——只支持 type 与 match"))
        type_name = raw.get("type")
        try:
            raise_type(type_name)
        except ValueError as exc:
            problems.append(self._error(flow, ("steps", index - 1, "raises", "type"),
                              f"步骤 {index} raises.type 不合法：{exc}"))
        match = raw.get("match")
        if match is not None:
            if not isinstance(match, str) or not match.strip():
                problems.append(self._error(flow, ("steps", index - 1, "raises", "match"),
                                  f"步骤 {index} raises.match 必须是非空正则字符串，"
                                  f"收到 {match!r}"))
            else:
                try:
                    re.compile(match)
                except re.error as exc:
                    problems.append(self._error(flow, ("steps", index - 1, "raises", "match"),
                                        f"步骤 {index} raises.match 不是合法正则：{exc}"))
        return problems

    def _check_page_decl(self, flow: dict, step: dict,
                         index: int) -> list[FlowValidationError]:
        """gap9b B-2 声明位**形态**判据——compile 静态校验段单点（`validate_flow` 本体第一步
        委托 compile，atlas compile / atlas run / export-pytest 自动同尺，#92 不留第二份）。

        两道闸分工（契约 B-2 钉死）：本道只管形态（非空标量/互斥/拒点分）；成员资格
        （`_pascal(page_object)` ∈ 实际生成类名集）归生成期第二道闸——components 只在
        生成期存在，dsl 拿不到，天然分层。**拒绝在构造任何 Task 之前**（原子应用纪律），
        消息点名步骤序号与值。键缺席 = 无声明，存量 flow 零涟漪（#115）。
        """
        problems: list[FlowValidationError] = []
        has_po = "page_object" in step
        has_lp = "legacy_pages" in step
        if has_po and has_lp:
            # 合一单键再按值分支=引擎猜语义（#11）；并列两键互斥是契约结论（#84 同族）
            return [self._error(flow, ("steps", index - 1),
                    f"步骤 {index} 同时带 page_object 与 legacy_pages：两条委托通道互斥"
                    f"（新页面对象 vs 旧套件函数），请二选一删掉另一键")]
        if has_po:
            value = step["page_object"]
            if not _decl_scalar_ok(value):
                problems.append(self._error(flow, ("steps", index - 1, "page_object"),
                              f"步骤 {index} 的 page_object 必须是非空标量（已批准组件名），"
                              f"收到 {value!r}：容器/空串/None/布尔一律拒绝，坏形态整体不产出"))
        if has_lp:
            value = step["legacy_pages"]
            if not _decl_scalar_ok(value):
                problems.append(self._error(flow, ("steps", index - 1, "legacy_pages"),
                              f"步骤 {index} 的 legacy_pages 必须是非空标量（旧套件函数名），"
                              f"收到 {value!r}：容器/空串/None/布尔一律拒绝，坏形态整体不产出"))
            elif "." in str(value).strip():
                # v1 稿的 <module.fn> 与既成形态不符（A.4）：模块位固定在产物常量，
                # 值只写函数名。此处不抄常量值（复制即第二份判据 #92），指常量名。
                problems.append(self._error(flow, ("steps", index - 1, "legacy_pages"),
                              f"步骤 {index} 的 legacy_pages 收到点分形态 {value!r}："
                              f"模块位由产物常量给出（产物 atlas_pages.py 的 "
                              f"LEGACY_PAGES_MODULE），声明只写函数名"))
        return problems

    def _check_step_id(self, flow: dict, step: dict, index: int,
                       declared: dict[str, int]) -> list[FlowValidationError]:
        """步骤 id：dispatch 靠它点名目标，重复 id 等于把选择权丢给运行期。"""
        raw = step.get("id")
        if raw is None:
            return []
        if not isinstance(raw, str) or not raw.strip():
            return [self._error(flow, ("steps", index - 1, "id"),
                                f"步骤 {index} 的 id 必须是非空字符串，收到 {raw!r}")]
        key = raw.strip()
        if key in declared:
            return [self._error(flow, ("steps", index - 1, "id"),
                                f"步骤 id {key!r} 重复（第 {declared[key]} 步与第 {index} 步）——"
                                f"dispatch 按 id 找人，重名就是让人猜")]
        declared[key] = index
        return []

    def _check_dispatch(self, flow: dict, steps: list,
                        declared: dict[str, int]) -> list[FlowValidationError]:
        """dispatch 目标集编译期校验（符合「不猜」红线）：

        default 必选（无隐式通道）、目标必须是已声明的步骤 id、目标必须在本路由之后
        （线性列表没有回边，指回前面那步早就执行过了）、一个分支只被一个路由认领
        （两处认领 = 运行期谁说了算无从判定，与 #84/#128 多生产者同键同红线）。

        **设计后果（现行规格，别当成漏检）**：运行期的过滤面是「被某个路由认领的步骤」
        （见 `_gated_map`），没被任何 `to`/`default` 认领的步骤不在路由管辖内、**恒执行**。
        所以作者删掉一条 lane 时必须同时删掉或挪走那一步，否则原分支变成无主步骤照样跑，
        而编译零警告——写操作场景下等于静默放大执行面。为什么不在此拒绝：判定「这一步本该
        属于本路由」需要猜作者的分支边界（路由区间是启发式，会把合法落空的普通步骤误报成
        错误），违「不猜」红线。预留收紧方案是显式的 `dispatch.exclusive: true`（声明本路由
        全量认领，漏认领即编译拒绝）——等出现真实多 lane flow、有人真删过 lane 再上，
        需求驱动，不给零使用的键加投机功能（红线 #12）。行为由
        `tests/test_dsl_template_dispatch.py::test_unclaimed_branch_step_runs_unconditionally`
        钉桩，翻语义必须先过该测与人审。
        """
        problems: list[FlowValidationError] = []
        owners: dict[str, int] = {}
        for index, step in enumerate(steps, 1):
            if not isinstance(step, dict) or "dispatch" not in step:
                continue
            where = ("steps", index - 1, "dispatch")
            raw = step["dispatch"]
            if not isinstance(raw, dict):
                problems.append(self._error(flow, where,
                              f"步骤 {index} dispatch 必须是映射（on/to），收到 "
                              f"{type(raw).__name__}：{raw!r}"))
                continue
            spec, collisions = _dispatch_map(raw)
            for collision in collisions:
                problems.append(self._error(flow, where, f"步骤 {index} {collision}"))
            unknown = sorted(key for key in spec if key not in {"on", "to"})
            if unknown:
                problems.append(self._error(flow, where,
                              f"步骤 {index} dispatch 未知字段：{unknown}——只支持 on 与 to"
                              f"（default 写在 to 里面）"))
            on = spec.get("on")
            if not isinstance(on, str) or not on.strip():
                problems.append(self._error(flow, where if "on" not in spec
                                            else where + ("on",),
                              f"步骤 {index} dispatch.on 必须是非空 ctx 字段名，收到 {on!r}"))
            to = spec.get("to")
            if not isinstance(to, dict) or not to:
                problems.append(self._error(flow, where if "to" not in spec else where + ("to",),
                              f"步骤 {index} dispatch.to 必须是非空映射"
                              f"（{{取值: 步骤id}} 且含 default），收到 {to!r}"))
                continue
            for value, branch in to.items():
                if isinstance(value, bool):
                    # y/yes/on/true 这类裸键被 YAML 1.1 解析成布尔，str(True)="True" 与
                    # ctx 里的 "y" 永远对不上——不拦就是每次运行静默走 default（#121 同族坑）
                    problems.append(self._error(flow, where + ("to",),
                                  f"步骤 {index} dispatch.to 的取值键 {value!r} 是 YAML 裸布尔，"
                                  f"与 ctx 里的字符串永远匹配不上——给该取值加引号"))
                    continue
                paths = [branch] if isinstance(branch, str) else branch
                if not isinstance(paths, list) or not paths or not all(
                        isinstance(item, str) and item.strip() for item in paths):
                    problems.append(self._error(flow, where + ("to",),
                                  f"步骤 {index} dispatch.to[{value!r}] 必须是步骤 id 或 id 列表，"
                                  f"收到 {branch!r}"))
                    continue
                for target in paths:
                    name = target.strip()
                    position = declared.get(name)
                    if position is None:
                        problems.append(self._error(flow, where + ("to",),
                                      f"步骤 {index} dispatch 目标 {name!r} 不存在——flow 里没有 "
                                      f"steps[].id={name!r}（已声明 id：{sorted(declared) or '无'}）"))
                        continue
                    if position <= index:
                        problems.append(self._error(flow, where + ("to",),
                                      f"步骤 {index} dispatch 目标 {name!r} 在第 {position} 步——"
                                      f"路由只能向后跳，前面的步骤已经执行过"))
                        continue
                    owner = owners.get(name)
                    if owner is not None and owner != index:
                        # 同一个路由的多条分支指向同一步是合法写法（A/B 都走 left）；
                        # 争同一个分支的是**两个不同路由**——运行期谁的决定说了算没定义
                        problems.append(self._error(flow, where + ("to",),
                                      f"步骤 {name!r} 同时被第 {owner} 步与第 {index} 步的 dispatch "
                                      f"认领——两处路由争一个分支，运行期按谁的决定走无从判定"))
                        continue
                    owners[name] = index
            if "default" not in to:
                problems.append(self._error(flow, where + ("to",),
                              f"步骤 {index} dispatch 缺 default——无隐式通道：取值没命中时"
                              f"必须有明确去处（to.default: [步骤id]）"))
        return problems

    def _check_asserts(self, flow: dict) -> list[FlowValidationError]:
        """assert 块引用与参数校验：与步骤同域，候选期就判，不等 run 时才暴露。

        参数合法性只能靠工厂兑现（`assert_api` 缺 expect 是工厂自己报的 ValueError），
        所以这里会**构造**候选——构造无副作用（perform 才碰浏览器/接口），
        产物用完即弃；引用的存在性则先看 registry，缺名时报错带 ^ 而不是裸 KeyError。
        """
        problems: list[FlowValidationError] = []
        raw = flow.get("assert")
        if raw is not None and not isinstance(raw, list):
            return [self._error(flow, ("assert",), "assert 块必须是列表")]
        for index, item in enumerate(raw or [], 1):
            if isinstance(item, dict) and "reconcile" in item:
                # en-15：对账形态编译期校验（left/right 必填非空、fields 是非空字符串列表）
                spec = item["reconcile"]
                if not isinstance(spec, dict):
                    problems.append(self._error(flow, ("assert", index - 1, "reconcile"),
                                      f"assert 块第 {index} 条 reconcile 必须是映射"
                                      f"（{{left, right, fields?}}），收到 "
                                      f"{type(spec).__name__}：{spec!r}"))
                    continue
                for side in ("left", "right"):
                    value = spec.get(side)
                    if not isinstance(value, str) or not value.strip():
                        problems.append(self._error(flow, ("assert", index - 1,
                                                          "reconcile", side),
                                            f"assert 块第 {index} 条 reconcile.{side} 必须是"
                                            f"非空字符串（ctx 路径或字面量），收到 {value!r}"))
                fields = spec.get("fields")
                if fields is not None:
                    if (not isinstance(fields, list) or not fields
                            or not all(isinstance(f, str) and f.strip() for f in fields)):
                        problems.append(self._error(flow, ("assert", index - 1,
                                                          "reconcile", "fields"),
                                            f"assert 块第 {index} 条 reconcile.fields 必须是"
                                            f"非空字符串列表（点路径），收到 {fields!r}"))
                unknown = sorted(k for k in spec
                                 if k not in {"left", "right", "fields"})
                if unknown:
                    problems.append(self._error(flow, ("assert", index - 1, "reconcile"),
                                      f"assert 块第 {index} 条 reconcile 未知字段：{unknown}"
                                      f"——只支持 left/right/fields（不做模糊匹配）"))
                continue
            if not isinstance(item, dict) or "task" not in item:
                continue
            name = item["task"]
            if not isinstance(name, str) or not self.registry.known(name):
                problems.append(self._error(flow, ("assert", index - 1, "task"),
                              f"assert 块第 {index} 条引用未注册的 Task：{name!r}"))
                continue
            try:
                self.registry.create(name, **{k: v for k, v in item.items() if k != "task"})
            except (TypeError, ValueError) as exc:
                problems.append(self._error(flow, ("assert", index - 1),
                              f"assert 块第 {index} 条 Task {name!r} 参数不合法：{exc}"))
        return problems

    def _check_slots(self, flow: dict) -> list[FlowValidationError]:
        """文本级 `{{slot}}` 残留 = 骨架没填完，不产出可执行 flow（abstract 语义）。

        有源文本按源文本扫（行号准）；内存 dict 退化为递归扫字符串值（点名内容与所在键，
        拿不到行号就不猜）。
        """
        document = _document(flow)
        if document is not None:
            error = _slot_error(document.source_text, document.source_name)
            return [error] if error else []
        found = [(path, match.group(1)) for path, text in _iter_strings(flow)
                 for match in _SLOT.finditer(text)]
        if not found:
            return []
        names = sorted({name for _, name in found})
        return [FlowValidationError(f"模板槽位未填全：{names}（位于 {list(found[0][0])}）——"
                                    f"骨架 flow 必须经 template+slots 实例化后才能编译")]

    def _check_produces(self, flow: dict, tasks: list[Task]) -> list[FlowValidationError]:
        """语义校验（需要 Task 元数据，故排在候选构造之后）：引用的 ctx 键必须有声明来源。

        步骤级 `requires` 与 `dispatch.on` 都是「读 ctx」的声明。合法来源只有三处：
        flow 级 `requires` 的数据落点、上游 Task 的 produces、上游步骤的 save_as
        （`_` 前缀是引擎内部键，如 legacy 桥接播种的 `_data`，不参与校验）。
        没有来源就是未定义引用——旧实现把 `requires` 当保留键**静默丢弃**，写错名字零报错，
        一路漂到运行期才以 KeyError/auto 降级形态炸出来（归因漂到远处，#106/#113 同族）。
        """
        problems: list[FlowValidationError] = []
        steps = _steps_of(flow)
        available = self._supply_targets(flow)
        for index, step in enumerate(steps, 1):
            if not isinstance(step, dict):
                continue
            own = set(tasks[index - 1].metadata.produces) if index <= len(tasks) else set()
            save_as = step.get("save_as")
            if isinstance(save_as, str) and save_as.strip():
                own.add(save_as.strip())
            for reference in _step_requires(step):
                if not _declared(reference, available):
                    problems.append(self._error(flow, ("steps", index - 1, "requires"),
                                  f"步骤 {index} requires 引用不存在：{reference!r}——flow 级 "
                                  f"requires 落点与上游步骤产出里都没有它（当前可用："
                                  f"{sorted(available) or '（暂无已声明键）'}）；引擎不猜数据从哪来"))
            router = _router_of(step, index)
            # 路由在内层 Task 跑完之后读值，所以本步骤自己的产出也算已声明（上游 ∪ 本步）；
            # 步骤级 requires 是「入口依赖」，本步产出不能拿来喂自己，仍只认上游。
            if router is not None and not _declared(router.on, available | own):
                problems.append(self._error(flow, ("steps", index - 1, "dispatch"),
                              f"步骤 {index} dispatch.on={router.on!r} 不是已声明 ctx 字段"
                              f"（当前可用：{sorted(available | own) or '（暂无已声明键）'}）——"
                              f"路由只读声明过的键，不猜运行时谁会写进来"))
            available |= own
        return problems

    @staticmethod
    def _supply_targets(flow: dict) -> set[str]:
        """flow 级 requires 的数据落点（`as` 优先，否则条目名）——provision 写进 ctx 的键。"""
        targets = set()
        for name, raw in (flow.get("requires") or {}).items():
            alias = raw.get("as") if isinstance(raw, dict) else None
            targets.add(str(alias) if alias else str(name))
        return targets

    def _check_await(self, flow: dict) -> list[FlowValidationError]:
        """en-10：flow 级 `await` 形态判据（compile 静态段，run/compile 自动同尺）。

        形态：`await: {kind: poll, timeout_ms: int>0, interval_ms: int>=0, assert: [...]}`。
        `kind` 本波只支持 `poll`（轮询既有 assert 块直到通过或超时）；`assert` 必填非空列表，
        复用既有 `_check_asserts` 的引用与参数校验；超时按 C9 口径落 waiting（dsl.run 现成的
        AwaitingHuman 腿），绝不静默变绿。键缺席 = 无声明，存量 flow 零涟漪。
        """
        problems: list[FlowValidationError] = []
        if "await" not in flow:
            return problems
        raw = flow["await"]
        if not isinstance(raw, dict):
            return [self._error(flow, ("await",),
                    f"flow 级 await 必须是映射（{{kind, timeout_ms, interval_ms?, assert}}），"
                    f"收到 {type(raw).__name__}：{raw!r}")]
        unknown = sorted(k for k in raw
                         if k not in {"kind", "timeout_ms", "interval_ms", "assert"})
        if unknown:
            problems.append(self._error(flow, ("await",),
                              f"flow 级 await 未知字段：{unknown}——只支持 kind/timeout_ms/"
                              f"interval_ms/assert"))
        kind = raw.get("kind")
        if kind != "poll":
            problems.append(self._error(flow, ("await", "kind"),
                              f"flow 级 await.kind 只支持 poll（轮询到 assert 通过或超时），"
                              f"得到 {kind!r}"))
        timeout = raw.get("timeout_ms")
        if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout <= 0:
            problems.append(self._error(flow, ("await", "timeout_ms"),
                              f"flow 级 await.timeout_ms 必须是正整数（毫秒），得到 {timeout!r}"))
        interval = raw.get("interval_ms", 0)
        if isinstance(interval, bool) or not isinstance(interval, int) or interval < 0:
            problems.append(self._error(flow, ("await", "interval_ms"),
                              f"flow 级 await.interval_ms 必须是非负整数（毫秒），"
                              f"得到 {interval!r}"))
        if timeout is not None and not isinstance(timeout, bool) \
                and isinstance(timeout, int) and interval is not None \
                and not isinstance(interval, bool) and isinstance(interval, int) \
                and interval > timeout:
            problems.append(self._error(flow, ("await", "interval_ms"),
                              f"flow 级 await.interval_ms={interval} 大于 timeout_ms={timeout}"
                              f"——轮询间隔超过总窗口，第一拍就超时"))
        sub_asserts = raw.get("assert")
        if not isinstance(sub_asserts, list) or not sub_asserts:
            problems.append(self._error(flow, ("await", "assert"),
                              f"flow 级 await.assert 必须是非空列表（复用既有 assert 形态），"
                              f"得到 {sub_asserts!r}"))
        else:
            # 复用既有 assert 块的引用/参数校验（同一把尺）：把子 assert 当独立 flow 校验。
            # 借用 document 的 ^ 指针时把路径从 ("assert", i) 平移到 ("await", "assert", i)。
            sub_flow = dict(flow)
            sub_flow["assert"] = sub_asserts
            for problem in self._check_asserts(sub_flow):
                if isinstance(problem, FlowReferenceError):
                    problems.append(problem)   # 引用类：消息已完整（含 ^ 或步骤序号），直接复用
                else:
                    problems.append(self._error(flow, ("await", "assert"), str(problem)))
        return problems

    def _error_message(self, flow: dict, path: tuple, message: str) -> str:
        document = _document(flow)
        return document.pointer(path, message) if document is not None else message

    def _error(self, flow: dict, path: tuple, message: str) -> FlowValidationError:
        """构建带 ^ 指针的异常：未定义引用类 → FlowReferenceError（同时是 KeyError），
        其余结构问题 → FlowValidationError；拿不到源文本时退化为纯消息（不猜行号）。"""
        reference = any(marker in message for marker in
                        ("未注册", "引用不存在", "dispatch 目标", "不是已声明", "槽位未填"))
        error = FlowReferenceError if reference else FlowValidationError
        return error(self._error_message(flow, path, message))

    def validate_flow(self, flow: dict) -> list[Task]:
        """结构校验的单一来源（compile 与 run 共用，杜绝「compile 说能跑、run 登录一半炸」）：
        `compile()` 本体就是原子校验（步骤形态/task 引用/channel/on_env_blocked/dispatch/
        模板槽位/assert 块/flow 级策略），这里只补数据供给块（落点冲突与条目形态）。
        全离线、零副作用。"""
        tasks = self.compile(flow)
        self._check_supply_targets(flow.get("requires"))   # #113 面C2：落点冲突登录前点名
        for name, raw in (flow.get("requires") or {}).items():
            self._check_data_policy(name, raw)
            DataRequirement.from_dict(raw)   # 复用 #94 的形态/entity 校验，登录前当场点名
        return tasks

    @staticmethod
    def _check_supply_targets(requires: dict) -> None:
        """#113 面C：两条 requires 的落点键（as 或条目名）相同 → 显式报错点名两条条目。
        探针实锤旧实现静默 last-wins：order_a(SalesOrder) 的数据被 order_b(PurchaseOrder)
        整条覆盖，下游读到 PO9 以为是 SO1 继续跑——与 #84 多生产者同产出键同红线
        （引擎无法判定两份数据等价，静默择一把一条供给翻成另一条）。
        单一来源校验：provision 执行前与 validate_flow（atlas compile / run 登录前）共用，
        杜绝「compile 说能跑、provision 静默覆盖」（#97 口径）。"""
        seen: dict[str, str] = {}
        for name, raw in (requires or {}).items():
            if not isinstance(raw, dict):
                continue  # 非映射交给 from_dict 点名「必须是映射」
            target = str(raw.get("as") or "") or name
            if target in seen:
                raise ValueError(
                    f"requires.{name} 与 requires.{seen[target]} 的数据落点同为 {target!r}："
                    f"静默后者覆盖前者，下游读到的是被遮蔽的数据。改 as 区分落点或删除一条")
            seen[target] = name

    def provision(self, flow: dict, actor) -> dict[str, dict]:
        if not self.provider:
            if flow.get("requires"):
                raise RuntimeError("Flow 声明了 requires，但未配置 DataProvider")
            return {}
        self._check_supply_targets(flow.get("requires"))
        result = {}
        for name, raw in flow.get("requires", {}).items():
            self._check_data_policy(name, raw)
            requirement = DataRequirement.from_dict(raw)
            try:
                supplied = self.provider.supply(requirement)
            except EnvBlocked:
                if raw.get("on_env_blocked") != "skip":
                    raise
                actor.ctx.setdefault("_data_skipped", []).append(name)
                continue
            key = requirement.as_ or name
            actor.ctx[key] = supplied.data
            result[key] = supplied.data
            actor.ctx.setdefault("_data_actions", {})[key] = supplied.action
        return result

    @staticmethod
    def _check_data_policy(name: str, raw) -> None:
        # from_dict 已拒非法 on_env_blocked，但报错不含是哪个 requires 键——多条件 flow 里
        # 「on_env_blocked 只支持 skip/stop」不带名字，人还得回头猜哪条写错。这里点名补归因。
        # 非映射 raw 交给 from_dict 报「必须是映射」（这里 raw.get 会裸 AttributeError）。
        if not isinstance(raw, dict):
            return
        policy = raw.get("on_env_blocked", "stop")
        if policy not in {"skip", "stop"}:
            raise ValueError(f"requires.{name} 的 on_env_blocked 只支持 skip/stop，收到 {policy!r}")

    def run(self, flow: dict, actor) -> dict:
        engine = None
        # 原子性：结构校验必须在 provision **之前**。requires 供给会在真实环境占建单配额、
        # 造出单据（红线 #14），坏 flow（第 3 步引用不存在的 Task）不该先把数据造出来再报错。
        tasks = self.validate_flow(flow)
        self.provision(flow, actor)
        if flow.get("engine"):
            from .engine import ExecutionEngine
            from .recorder import RunRecorder
            config = flow["engine"]
            # C6 版本三元组（引擎版本 + 知识库指纹 + flow 摘要）随 run 记录落盘——
            # 「上周能跑今天炸」的第一手对照物。flow 是当前参数：文件加载的带 md5
            # （kind=source），内存构造的走派生 sha1（kind=derived），降级形态明写。
            recorder = RunRecorder(flow.get("id", "flow"), config.env,
                                   config.reports_dir / "runs.jsonl", config.mode,
                                   version=versioning.version_triple(
                                       private_root=versioning.private_knowledge_root(actor),
                                       flow=flow))
            engine = ExecutionEngine(config, recorder=recorder, session=getattr(actor, "session", None),
                                     contracts=getattr(actor, "contracts", None),
                                     knowledge=getattr(actor, "knowledge", None),
                                     healer=getattr(actor, "healer", None),
                                     journal=getattr(actor, "journal", None),
                                     evidence=getattr(actor, "evidence", None),
                                     case_id=flow.get("id", "flow"))
        flow_policy = flow.get("on_env_blocked", "stop")
        gated = _gated_map(flow)
        status = "passed"
        # C9 run 级 reason：waiting 与 env_blocked 两条腿非空（后者 2026-11-06 接通）。
        # 其余终态恒空串 → recorder.close **不写键**（passed/failed/interrupted 行逐字节
        # =旧形态，兼容表 §6 的全等钉仍成立）。
        run_reason = ""
        executed: list[str] = []
        try:
            for index, task in enumerate(tasks, 1):
                branch = _step_id(flow, index)
                gate = gated.get(branch)
                if gate is not None and branch not in _selected(actor, gate):
                    # 未被本路由选中的分支：整步不执行、不进 runs.jsonl——跳过的分支既不是
                    # 「通过了」也不是「被环境挡了」，它压根不在本次路径上（记成任一都是假象）
                    continue
                bound = FunctionalTask(task.metadata,
                    lambda a, original=task: _perform_interpolated(original, a), task.name)
                if engine:
                    engine.execute(actor, bound)
                else:
                    # 与 engine.execute 同口径的「skip 不写产出」：Actor.attempts_to 无条件
                    # 把 perform 的返回写进声明键，被 on_env_blocked: skip 跳过的步骤返回 None
                    # → ctx 里就多出一个「键在、值是 None」的假产出。下游 dispatch 会把它当
                    # 有值直接选分支（本该按数据缺失终止），断言也会把 None 当真实结果比对。
                    # 引擎那条腿早就堵过（engine.py 的 skipped 分支），这里是漏网的第二条腿。
                    previous = {key: actor.ctx.get(key, _MISSING)
                                for key in task.metadata.produces}
                    actor.attempts_to(bound)
                    if actor.ctx.get("_steps_skipped", [])[-1:] == [task.name]:
                        for key, value in previous.items():
                            if value is _MISSING:
                                actor.ctx.pop(key, None)
                            else:
                                actor.ctx[key] = value
                executed.append(task.name)
            self._run_asserts(flow, actor, engine)
            if flow.get("await"):
                self._run_await(flow, actor, engine)
        except CONTROL_FLOW:
            # 操作者 Ctrl-C / 显式退出 / 生成器关闭不是产品或用例失败：如实记 interrupted，
            # 避免 atlas summary 把「中断的跑」计进 failed 污染 pass/fail 真相（红线 #7 环境/操作
            # 与缺陷分开）。#129：判据收敛 failures.CONTROL_FLOW 单一真相。仍原样向上抛，
            # 退出码/中断语义不变。
            status = "interrupted"
            raise
        except AwaitingHuman as exc:
            # C9 显态：跑停在「等人处置」的位置上——既不是缺陷（不进 failed 缺陷分布），
            # 也不是中断（没人终止它，是流程自己声明在等谁）。次序钉死在 CONTROL_FLOW
            # 之后、EnvBlocked 之前：终止信号优先（人 Ctrl-C 打断的正是等人工那一步，语义
            # 仍是 interrupted）；与 env_blocked 互斥（一个是策略挡、一个是人在场）。
            # 取值走 FailureClass.WAITING.value——**判据/落盘取值单一出处**（#92），
            # 不在这里裸写字符串；reason 走 exception_brief（净化刀，与 degraded_to_ui
            # 同一把 C4②），截 200 字符防超长报文淹没 run 记录。
            # 原样重抛：rc 由 CLI 转 3（等人信号），进程收尾照常走 __exit__/recorder。
            status = FailureClass.WAITING.value
            run_reason = exception_brief(exc)[:200]
            raise
        except EnvBlocked as exc:
            status = "env_blocked"
            # reason 写侧接通（2026-11-06，原「不做本波」项放行）：env_blocked 行此前只把
            # 根因塞进 actor.ctx["_env_blocked"]（活在内存里，跑完即丢），runs.jsonl 只留
            # 一个 `env_blocked` 字符串——批量巡检里 30 条同样写着 env_blocked 的行**分不清
            # 哪条是「SIT 没价格表」哪条是「PT 字典缺值」**，处置动作（补数据 vs 找环境）
            # 全靠回放到现场重看。形态与 waiting 腿完全同款：exception_brief 净化刀 + 截 200。
            run_reason = exception_brief(exc)[:200]
            actor.ctx.setdefault("_env_blocked", str(exc))
            if flow_policy == "skip":
                actor.ctx.setdefault("_flow_skipped", True)
            else:
                raise
        except BaseException:
            status = "failed"
            raise
        finally:
            if engine:
                # reason 只有 waiting / env_blocked 两条腿非空；其余终态传空串=recorder 不写键
                # （存量行零涟漪）
                engine.recorder.close(status, reason=run_reason)
                # 落库线「跑成功→自动固化」：harvest:true 且整条跑绿才固化——失败/中断/
                # env_blocked/waiting 都不是可复用资产，一律不进塔。固化在 recorder.close
                # **之后**：runs.jsonl 的终态行先落盘，固化只是它的下游。
                if status == "passed" and flow.get("harvest") is True:
                    _harvest_passed_flow(flow, engine)
        return {"status": status,
                "steps": [r.task for r in engine.results] if engine else executed}

    def _run_await(self, flow: dict, actor, engine=None) -> None:
        """en-10：flow 级 `await: {kind: poll, ...}` 轮询既有 assert 块直到通过或超时。

        语义：
        - 每拍跑一次 `assert` 子块（复用 `_run_asserts` 同一把刀：字符串表达式 / dict task /
          key=value / reconcile 全都可用）；子块**全部通过** = await 兑现，返回；
        - 子块抛 `AssertionError`（含 `ReconciliationMismatch`，同族）= 本拍未就绪，
          睡 `interval_ms` 后下一拍；**不洗绿**——超时仍没就绪就抛 `AwaitingHuman`，
          由 `dsl.run` 现成的 waiting 腿收口（status=waiting + rc3 + reason 净化），不伪装通过；
        - 子块抛**非断言**异常（业务/契约/会话/网络）= 真失败，原样传播（不等也不吞）——
          await 是「等条件就绪」，不是「把失败按住」；
        - 控制流（CONTROL_FLOW）恒穿透（#114）；
        - 等待现场不写产出、不改 ctx（`_run_asserts` 里 dict-task 形态的 assert 若声明
          save_as 会写 ctx——那是既有 assert 语义，await 不新增写路径）。

        首拍立即执行（不先睡 interval）——条件已就绪时零额外等待；轮询时钟用 `time.monotonic`
        （不受系统墙钟跳变影响），`sleep` 走 `time.sleep`（本波不做注入位：dsl.run 无 sleep
        参数先例，测试用小 timeout_ms+大 interval_ms 让真实等待有界）。
        """
        import time
        spec = flow["await"]
        timeout_ms = int(spec["timeout_ms"])
        interval_ms = int(spec.get("interval_ms", 0))
        sub_flow = dict(flow)
        sub_flow["assert"] = spec["assert"]
        deadline = time.monotonic() + timeout_ms / 1000.0
        while True:
            try:
                self._run_asserts(sub_flow, actor, engine)
                return   # 子块全部通过：条件就绪
            except AssertionError:
                pass     # 未就绪，继续轮询（超时仍在下面判）
            if time.monotonic() >= deadline:
                raise AwaitingHuman(
                    f"await poll 超时：{timeout_ms}ms 内 assert 未通过（interval_ms="
                    f"{interval_ms}）——外部事件未就绪，等人工核实；绝不静默变绿")
            if interval_ms > 0:
                time.sleep(interval_ms / 1000.0)

    def _run_asserts(self, flow: dict, actor, engine=None) -> None:
        """执行 flow 的 assert 块：表达式断言 + 内联 Task 断言。

        方案 3.3 承诺的 assert 块支持两种形式：
          - 字符串 "pt_echo == source_order.pt"：两侧用 interpolate 解析后比较
          - dict 带 task 键：编译成 Task 执行（如 assert_api）
          - dict 不带 task 键：键值断言，逐条比较 ctx 值
        """
        asserts = flow.get("assert")
        if not asserts:
            return
        if not isinstance(asserts, list):
            raise ValueError("assert 块必须是列表")
        for index, item in enumerate(asserts, 1):
            if isinstance(item, str):
                self._assert_expression(item, actor, index)
            elif isinstance(item, dict):
                if "reconcile" in item:
                    # en-15：跨模块对账（两侧 save_as + 字段映射 + 值级 diff），只读无副作用
                    _ReconcileAssert(item, index).run(actor)
                elif "task" in item:
                    task = self.registry.create(item["task"], **{
                        k: v for k, v in item.items() if k != "task"})
                    bound = FunctionalTask(task.metadata,
                        lambda a, original=task: _perform_interpolated(original, a), task.name)
                    if engine:
                        engine.execute(actor, bound)
                    else:
                        actor.attempts_to(bound)
                else:
                    for key, expected in item.items():
                        actual = interpolate(f"${{{key}}}", actor.ctx)
                        expected = interpolate(expected, actor.ctx)
                        if actual != expected:
                            raise AssertionError(
                                f"assert 块第 {index} 条：{key} 期望 {expected!r}，实际 {actual!r}")
            else:
                raise ValueError(f"assert 块第 {index} 条只支持字符串或 dict")

    def _assert_expression(self, expr: str, actor, index: int) -> None:
        """解析 a OP b 表达式，两侧用 ctx 插值后比较。

        支持 ==、!=、>=、<=、>、<。两侧先尝试当 ctx 路径解析，失败则当字面量。
        """
        m = _ASSERT_EXPR.match(expr)
        if not m:
            raise ValueError(f"assert 块第 {index} 条表达式缺少比较运算符：{expr!r}")
        left_str, op, right_str = m.group(1).strip(), m.group(2), m.group(3).strip()
        left_value = self._resolve_operand(left_str, actor, index)
        right_value = self._resolve_operand(right_str, actor, index)
        try:
            ok = _ASSERT_OPS[op](left_value, right_value)
        except TypeError as exc:
            raise AssertionError(
                f"assert 块第 {index} 条：{left_str}({left_value!r}) {op} {right_str}({right_value!r}) 类型不可比较：{exc}")
        if not ok:
            raise AssertionError(
                f"assert 块第 {index} 条：{left_str}({left_value!r}) {op} {right_str}({right_value!r}) 不成立")

    @staticmethod
    def _resolve_operand(operand: str, actor, index: int):
        """先当 ctx 路径解析，失败则当字面量（数字转 int/float，否则字符串）。

        #113 面A：路径形态操作数（word.word）解析失败不得静默回落成字符串字面量——
        探针实锤 `source_order.ptx != source_order.ptx2` 两个打错的路径被当成互不相等的
        字符串比较，断言恒真通过（假绿零验证，写错的断言比没有断言更危险——它声称验证过
        而什么都没验）。字面量兼容面保留：无点单词（NET30/SO1）与数字（3.14 数字开头
        不匹配路径形态）照常回落。
        #113 面B：true/false/none/null 识别为 bool/None 字面量——YAML 作者自然写
        `ok == true`，旧实现 True 与字符串 'true' 比较，ctx 真为 True 反而断言失败
        （假阴性：正确断言被拒，人被迫改成删除该断言——比假绿更常被真实遭遇）。
        """
        if _PATH_OPERAND.fullmatch(operand):
            try:
                return _lookup(actor.ctx, operand)
            except KeyError as exc:
                raise AssertionError(
                    f"assert 块第 {index} 条：操作数 {operand!r} 形如 ctx 路径但无法解析——"
                    f"要么补全该步骤产出、要么改为带引号的字面量：{exc}") from exc
        try:
            return _lookup(actor.ctx, operand)
        except (KeyError, AttributeError, TypeError):
            pass
        # 带引号字面量（'NET30.PT' / "true"）：去掉包裹引号按字符串比较——
        # 报错口径「改为带引号的字面量」必须真能用，否则引号进值永不相等。
        if len(operand) >= 2 and operand[0] == operand[-1] and operand[0] in "\"'":
            return operand[1:-1]
        low = operand.lower()
        if low in ("true", "false"):
            return low == "true"
        if low in ("none", "null"):
            return None
        try:
            return int(operand)
        except ValueError:
            pass
        try:
            return float(operand)
        except ValueError:
            return operand


def _steps_of(flow: dict) -> list:
    steps = flow.get("steps")
    return steps if isinstance(steps, list) else []


def _fingerprint(flow: dict, generation: int) -> str | None:
    """编译缓存键：源文本 md5 + 当前 steps 结构指纹 + 注册表代次；无源文本 = 不缓存。

    为什么不能只用源文本 md5：flow dict 是可变的（脚本 `flow["steps"] += extra`、CLI 注入
    engine 之后又改一步都很常见），md5 记的是**文件**而不是**这次拿进来的内容**——
    只认 md5 会把旧编译结果盖在新 steps 上放行，改掉的 `dispatch.on`/`channel` 静默不生效
    （#102「坏形态伪装成功」同族，只是这里伪装的是缓存命中）。
    steps 结构指纹取 repr：YAML 解出来的都是标量/容器，同进程内函数型参数（ui_action 的
    action）地址稳定，作为键的一部分只会因为对象更换而失效——保守方向，不会串味。
    """
    document = _document(flow)
    if document is None:
        return None
    shape = hashlib.md5(repr(_steps_of(flow)).encode("utf-8")).hexdigest()
    return f"{document.md5}:{shape}:{generation}"


_MISSING = object()   # ctx 快照哨兵：区分「键本来不存在」与「键存在且值为 None」


def _requires_shape_ok(raw) -> bool:
    if isinstance(raw, str):
        return bool(raw.strip())
    return (isinstance(raw, list) and bool(raw)
            and all(isinstance(item, str) and item.strip() for item in raw))


def _decl_scalar_ok(value) -> bool:
    """gap9b B-2 声明位「非空标量」判据（#117 单号/#134 坐标同纪律）：None/布尔/容器/空串
    与纯空白一律拒绝；数字标量按「非空标量」放行（成员资格归第二道闸，不在形态层猜）。"""
    if isinstance(value, bool) or value is None:
        return False
    if isinstance(value, (dict, list, tuple, set)):
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return isinstance(value, (int, float))


def _step_requires(step: dict) -> list[str]:
    raw = step.get("requires")
    if raw is None:
        return []
    return [raw] if isinstance(raw, str) else list(raw)


def _declared(reference: str, available: set[str]) -> bool:
    """引用根段是否已声明；`_` 前缀是引擎内部键（legacy 桥接播种的 `_data` 等），不校验。"""
    root = str(reference).split(".")[0]
    return root in available or root.startswith("_")


def _gate_of(step: dict, index: int) -> str:
    step_id = step.get("id")
    return step_id.strip() if isinstance(step_id, str) and step_id.strip() else f"#{index}"


def _router_of(step: dict, index: int) -> _Router | None:
    """把已通过静态校验的 dispatch 映射落成 _Router（纯搬运，不再判断合法性）。"""
    raw = step.get("dispatch")
    if not isinstance(raw, dict):
        return None
    raw, _ = _dispatch_map(raw)
    to = raw.get("to") or {}
    lanes: dict[str, list[str]] = {}
    for value, branch in to.items():
        if value == "default":
            continue
        items = [branch] if isinstance(branch, str) else list(branch)
        lanes[str(value)] = [item.strip() for item in items]
    default_raw = to.get("default")
    default = ([default_raw.strip()] if isinstance(default_raw, str)
               else [item.strip() for item in (default_raw or [])])
    return _Router(gate=_gate_of(step, index), on=str(raw.get("on", "")).strip(),
                   lanes=lanes, default=default)


def _gated_map(flow: dict) -> dict[str, str]:
    """分支步骤 id -> 认领它的路由 gate。无 dispatch 时是空表（run 循环零额外开销）。"""
    gated: dict[str, str] = {}
    for index, step in enumerate(_steps_of(flow), 1):
        if not isinstance(step, dict):
            continue
        router = _router_of(step, index)
        if router is None:
            continue
        for branch in list(router.lanes.values()) + [router.default]:
            for target in branch:
                gated[target] = router.gate
    return gated


def _step_id(flow: dict, index: int) -> str:
    """第 index 步（1 起）声明的步骤 id；没写就返回空串（不参与路由过滤）。"""
    steps = flow.get("steps") or []
    if index - 1 >= len(steps) or not isinstance(steps[index - 1], dict):
        return ""
    raw = steps[index - 1].get("id")
    return raw.strip() if isinstance(raw, str) else ""


def _selected(actor, gate: str) -> list[str]:
    return list(actor.ctx.get("_dispatch_selected", {}).get(gate, []))


def _iter_strings(value, path: tuple = ()):
    """递归吐出 (路径, 字符串值)：内存构造的 flow 用它扫残留槽位（无源文本时的退化路径）。"""
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _iter_strings(item, path + (key,))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _iter_strings(item, path + (index,))
    elif isinstance(value, str):
        yield path, value


def _harvest_passed_flow(flow: dict, engine) -> None:
    """跑绿 → 固化（落库线「跑成功→自动固化」半边）。

    失败草稿通路早已存在（engine.execute 失败路径 journal.draft），成功路径此前是空白：
    用例跑绿后动作序列就散了，不成资产。本钩子把 harvest:true 且整条跑绿的 flow 声明成
    pending_review 的 flow_candidate 草稿进审核塔——动作序列原文（steps/assert，含 ${var}
    占位不改写）+ C2 成功三元组（verdict="pass"，engine._verification 单一真相）+
    run_id/env（可回查 runs.jsonl 对应行）。人审批准后落公共任务库
   （KnowledgeBase.save_task_entry）；只进塔不写知识（红线 #9/#10）。

    尽力而为（红线 #7 的另一面：不许把已通过的 run 翻成失败）：固化写盘崩（盘满/权限/
    队列坏行之外的真异常）只 stderr WARN 留痕，run 终态与返回值纹丝不动。无 journal
    （纯 DSL 装配没接落盘通道）就如实跳过——不猜落点，绝不静默假装固化了。
    """
    import sys
    journal = getattr(engine, "journal", None)
    if journal is None or not hasattr(journal, "append_draft"):
        return
    try:
        steps = [dict(item) for item in flow.get("steps", []) if isinstance(item, dict)]
        evidence = engine._verification(True)   # C2 三元组单一真相（失败路径同一把刀）
        evidence.update({"run_id": engine.recorder.run_id,
                         "env": str(getattr(engine.config, "env", "") or ""),
                         "steps": steps})
        assert_block = flow.get("assert")
        if isinstance(assert_block, list):
            evidence["assert"] = assert_block
        journal.append_draft(journal.draft_flow(
            case=str(flow.get("id", "flow") or "flow"),
            digest=versioning.flow_digest(flow), evidence=evidence))
    except Exception as exc:
        # 显示段 ASCII 前缀（X4，GBK 控制台不崩）；异常文本截断防长报文淹没终端
        print(f"WARN: harvest 固化失败（run 结果不受影响）: {str(exc)[:160]}",
              file=sys.stderr, flush=True)


def _combine(problems: list[FlowValidationError]) -> FlowValidationError:
    """多个问题一次报全（LiteFlow 同款）：首条完整消息 + 其余条目的首行清单。

    异常类型取首条的类型——引用类（FlowReferenceError）同时是 ValueError/KeyError，
    调用方两类都接得住；逐条完整消息带各自的 ^ 指针。
    """
    first = problems[0]
    if len(problems) == 1:
        return first
    rest = "\n".join(f"  - {str(item).splitlines()[0]}" for item in problems[1:])
    return type(first)(f"{first}\n另有 {len(problems) - 1} 处编译问题：\n{rest}")


class _ChannelTask(Task):
    """把步骤的 channel/intent 覆盖编译进 Task，不改动业务 Task 本体。

    为什么不再是原来的 `FunctionalTask(lambda: original.perform(actor))`：真实 Task 被关在
    闭包里，不在包装器的 `__dict__` 里，而 run() 的插值是按属性遍历的——于是
    「写了 channel 覆盖的步骤」+「参数里有 `${...}`」= 参数原样字面量发给系统（静默不生效，
    与 legacy_case 文档里点名过的「intent/read_only 写了也不透传」同一族死配置）。
    显式持有 inner 并在 perform 里把插值透传下去，形态与 `_GuardedTask`/`_RouterTask` 一致。
    """
    def __init__(self, inner: Task, step: dict):
        self.inner = inner
        original = inner.metadata
        self.metadata = TaskMetadata(domain=original.domain,
            intent=step.get("intent", original.intent),
            requires=original.requires, produces=original.produces,
            channel=Channel(step["channel"]), ui_fallback=original.ui_fallback,
            api_operation=original.api_operation, read_only=original.read_only,
            tags=original.tags)

    @property
    def name(self) -> str:
        return self.inner.name

    def perform(self, actor):
        return _perform_interpolated(self.inner, actor)


class _GuardedTask(Task):
    """把 on_env_blocked 策略从 YAML 编译进 Task，不改动业务 Task 本体。

    与 dispatch 的分工（向后兼容，语义不变）：`on_env_blocked` 是**终态处理策略**
    （环境挡住这一步时 skip 还是 stop），`dispatch` 是**路由**（按已声明 ctx 字段的取值
    选后续分支，目标集编译期校验、default 必选）。新用例的分支选择请用 dispatch：
    skip/stop 把「没跑」与「跑了但被环境挡」压进同一个 ctx 键，而路由分支在 runs.jsonl
    里根本不出现，两者混用会看不清真实路径。
    """
    def __init__(self, inner: Task, policy: str):
        self.inner = inner
        self.policy = policy
        self.metadata = inner.metadata

    @property
    def name(self) -> str:
        return self.inner.name

    def perform(self, actor):
        try:
            # 内层 Task 的 `${...}` 参数必须由这里插值：外层 run 只对**包装器**做
            # _perform_interpolated，包装器的 __dict__ 里 inner 是个对象（不是字符串），
            # 插值遍历到它就停——旧实现在这一步直接 inner.perform，于是
            # 「on_env_blocked + ${var}」组合下的参数原样字面量发给系统（静默不生效）。
            return _perform_interpolated(self.inner, actor)
        except EnvBlocked:
            if self.policy == "skip":
                actor.ctx.setdefault("_steps_skipped", []).append(self.name)
                return None
            raise


class _NoopTask(Task):
    """纯路由节点的载体：dispatch 步骤没写 task 时用它占位（保持 steps 与 Task 列表 1:1）。

    不做任何业务动作，只让 `_RouterTask` 有一层可读的 metadata/name；name 带上 gate，
    runs.jsonl 里能看到「这里做过一次路由决定」而不是无声消失。
    """
    def __init__(self, gate: str):
        self.gate = gate
        self.metadata = TaskMetadata(domain="dsl", intent=f"dispatch 路由 {gate}")

    @property
    def name(self) -> str:
        return f"dispatch:{self.gate}"

    def perform(self, actor):
        return None


class _RouterTask(Task):
    """dispatch 路由节点：先跑内层 Task（写了 task 时），再按 ctx 取值选中分支。

    选中结果写进 `ctx["_dispatch_selected"][gate]`，由 `FlowCompiler.run()` 遍历线性列表时
    过滤未选中分支。路由决策只在执行期发生一次，而目标集、default、「on 是已声明字段」
    全部在编译期定死（不猜运行时谁写 ctx）。`ctx["_dispatch_routes"]` 留每次路由的
    取值与去向；engine 执行路径下由 `ExecutionEngine._flush_routes` 并入路由节点的
    step 记录真实落盘 runs.jsonl（#106 同族：ctx 内部键无消费方=审计链断点），
    事后审计「这轮走的哪条分支」有盘可查。
    """
    def __init__(self, inner: Task, router: _Router):
        self.inner = inner
        self.router = router
        self.metadata = inner.metadata

    @property
    def name(self) -> str:
        return self.inner.name

    def perform(self, actor):
        result = _perform_interpolated(self.inner, actor)
        try:
            value = _lookup(actor.ctx, self.router.on)
        except KeyError as exc:
            # 声明过但运行时没值 = 产出它的步骤没跑（或被跳过/分支未选中）：
            # 数据缺失明确终止，不猜取值（红线 #6），也不静默走 default 假装路由成功
            raise EnvBlocked(f"dispatch.on={self.router.on!r} 运行时不在 ctx：{exc}"
                             f"——产出该键的步骤没跑过；路由不猜取值") from exc
        selected = self.router.select(value)
        actor.ctx.setdefault("_dispatch_selected", {})[self.router.gate] = selected
        actor.ctx.setdefault("_dispatch_routes", []).append(
            {"gate": self.router.gate, "on": self.router.on, "value": str(value),
             "selected": list(selected)})
        return result


class _RaisesTask(Task):
    """en-12：负向断言包装器——期待内层 Task 抛出指定异常（含 ApiEvidence/环境分类口径
    不误伤）。

    语义（design_en §en-12）：
    - 内层**抛出** `type` 指定的异常（或其子类）→ **本步通过**（异常被吞，返回 None；
      `match` 给了时还需异常文本 `re.search` 命中——文本不命中算断言失败，不洗绿）；
    - 内层**没抛**任何异常 → 抛 AssertionError 点名「期望抛 X 但正常返回」（不洗绿）；
    - 内层抛了**别的**异常 → 原样传播（不是期望的那一类，不吞、不翻成断言失败——
      归因仍指真因，让 classify_failure/heal 走原本路径）；
    - 控制流（CONTROL_FLOW）**恒穿透**：Ctrl-C 不是「期望的抛」，与 healing 的 #108 同纪律。

    与 heal/auto 降级链的交互（钉测）：包装器在外层，**先于** engine 的 auto 降级闸与
    heal 判据看到异常——命中即吞、没命中即传播，两条链都不再触发（「抛了期望异常」就是
    本步的正常完成形态，不是待诊断缺陷）。所以：`raises` 命中 = 通过；未抛/文本不命中 =
    AssertionError（engine 归 ASSERTION，不进 heal 不进降级）；其他异常 = 真因传播。

    只读：包装器不改 metadata、不改 ctx、不写产出（内层若有产出且命中抛出，产出未发生
    = 不写，与 skip 步骤同口径）。
    """
    def __init__(self, inner: Task, spec: dict, step_index: int):
        self.inner = inner
        self.step_index = step_index
        # 构造期已由 _check_raises 校验过形态；此处再解析一次（同把 raise_type 单点），
        # 拿到异常类缓存住——运行期不再碰 builtins。
        self.expected_type = raise_type(spec["type"])
        self.match = spec.get("match")
        self.metadata = inner.metadata

    @property
    def name(self) -> str:
        return self.inner.name

    def perform(self, actor):
        try:
            result = _perform_interpolated(self.inner, actor)
        except CONTROL_FLOW:
            raise   # 终止信号不是「期望的抛」，恒穿透（#108/#114 同纪律）
        except BaseException as exc:
            if not isinstance(exc, self.expected_type):
                raise   # 抛了别的异常：真因传播，不吞不翻
            text = failures.exception_text(exc)
            if self.match and not re.search(self.match, text):
                raise AssertionError(
                    f"步骤 {self.step_index} 的 raises 命中异常 {type(exc).__name__} 但文本"
                    f"不匹配 /{self.match}/：{failures.exception_message(exc)}") from exc
            return None   # 命中：异常被吞，本步通过
        raise AssertionError(
            f"步骤 {self.step_index} 期望抛出 {self.expected_type.__name__}"
            f"{'（文本匹配 /' + self.match + '/）' if self.match else ''}，"
            f"但正常返回 {result!r}——负向断言未兑现")


class _ReconcileAssert:
    """en-15：跨模块对账 assert（assert 块里的 `reconcile:` 形态）——只读、无副作用。

    形态（assert 块条目）：
        - reconcile: {left: <ctx 路径或字面量>, right: <同>, fields: [点路径, ...]}

    语义：两侧都用 `interpolate` 解析（路径/字面量同 assert 表达式口径），可选 `fields`
    做值级 diff（点路径下钻，路径不可解析记 `<missing>` 且算不等）；**不做模糊匹配**——
    相等判据就是 `==`（数字/字符串不互转，与 assert 表达式 `==` 同把刀）。失败输出带
    两侧值与路径，方便人比对；无 fields 时整值比。
    """

    def __init__(self, spec: dict, index: int):
        self.spec = spec
        self.index = index

    def run(self, actor) -> dict:
        raw = self.spec["reconcile"]
        left_expr, right_expr = raw["left"], raw["right"]
        fields = raw.get("fields") or []
        left = interpolate(left_expr, actor.ctx)
        right = interpolate(right_expr, actor.ctx)
        diff = reconcile_diff(left, right, fields=list(fields),
                              left_label=str(left_expr), right_label=str(right_expr))
        if diff["equal"]:
            return {"reconcile": True, "left": str(left_expr), "right": str(right_expr),
                    "fields": list(fields), "mismatches": []}
        rows = "; ".join(
            f"path={m['path']!r} left={m['left']!r} right={m['right']!r}"
            for m in diff["mismatches"])
        raise ReconciliationMismatch(
            f"assert 块第 {self.index} 条 reconcile 失败：left={left_expr!r} 与 "
            f"right={right_expr!r} 不一致（fields={list(fields) or '<整值>'}）——{rows}")


def _perform_interpolated(task: Task, actor) -> Any:
    if hasattr(task, "__dict__"):
        original = dict(task.__dict__)
        try:
            for key, value in original.items():
                if key != "metadata":
                    setattr(task, key, interpolate(value, actor.ctx))
            return task.perform(actor)
        finally:
            for key, value in original.items():
                setattr(task, key, value)
    return task.perform(actor)
