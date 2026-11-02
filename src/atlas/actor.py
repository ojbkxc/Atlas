from __future__ import annotations
from typing import Any, Iterable, Type, TypeVar
from .abilities import BrowseTheWeb, CallApi
from .core import Ability, Task

A = TypeVar("A", bound=Ability)

class Actor:
    """跨 UI/API 的执行主体；ctx 只保存本业务流程上下文。"""
    def __init__(self, name: str = "Atlas", abilities: Iterable[Ability] = ()):
        self.name = name
        self.abilities: dict[type[Ability], Ability] = {}
        self.ctx: dict[str, Any] = {}
        for ability in abilities:
            self.can(ability)

    def can(self, ability: Ability) -> "Actor":
        self.abilities[type(ability)] = ability
        return self

    def using(self, ability_type: Type[A]) -> A:
        try:
            return self.abilities[ability_type]  # type: ignore[return-value]
        except KeyError as exc:
            raise RuntimeError(f"Actor {self.name} 未配置 {ability_type.__name__}") from exc

    @property
    def page(self) -> Any:
        return self.using(BrowseTheWeb).page

    @property
    def api(self) -> CallApi:
        return self.using(CallApi)

    def bake_session(self) -> None:
        self.api.bake_from_playwright(self.page.context)

    def attempts_to(self, *tasks: Task) -> "Actor":
        for task in tasks:
            result = task.perform(self)
            for key in task.metadata.produces:
                self.ctx[key] = result[key] if isinstance(result, dict) and key in result else result
        return self
