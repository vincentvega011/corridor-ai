"""A tiny in-process fake of an OpenAI compatible server, built on httpx.MockTransport."""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx


@dataclass
class Call:
    request: httpx.Request


@dataclass
class Route:
    url: str
    responses: list[Any] = field(default_factory=list)
    default: Any = None
    calls: list[Call] = field(default_factory=list)

    @property
    def call_count(self) -> int:
        return len(self.calls)

    def mock(self, return_value: Any = None, side_effect: Any = None) -> "Route":
        self.default = return_value
        if isinstance(side_effect, Exception):
            self.default = side_effect
        else:
            self.responses = list(side_effect or [])
        return self


class FakeServer:
    def __init__(self) -> None:
        self.routes: dict[str, Route] = {}
        self.transport = httpx.MockTransport(self._handle)

    def post(self, url: str) -> Route:
        route = Route(url)
        self.routes[url] = route
        return route

    def reset(self) -> None:
        self.routes.clear()

    def _handle(self, request: httpx.Request) -> httpx.Response:
        route = self.routes.get(str(request.url))
        if route is None:
            raise httpx.ConnectError("no route", request=request)
        route.calls.append(Call(request))
        item = route.responses.pop(0) if route.responses else route.default
        if isinstance(item, Exception):
            raise item
        if callable(item):
            item = item(request)
        assert isinstance(item, httpx.Response)
        return item


__all__ = ["Call", "Callable", "FakeServer", "Route"]
