# ruff: noqa: D100, D101, D102, D103, D104, D107, N999
from __future__ import annotations

from enum import StrEnum

from redux import BaseAction, BaseEvent


class Key(StrEnum):
    BACK = 'Back'
    HOME = 'Home'
    UP = 'Up'
    DOWN = 'Down'
    L1 = 'L1'
    L2 = 'L2'
    L3 = 'L3'


class KeypadAction(BaseAction):
    key: Key


class KeypadKeyUpAction(KeypadAction): ...


class KeypadKeyDownAction(KeypadAction): ...


class KeypadKeyPressAction(KeypadAction): ...


class KeypadEvent(BaseEvent):
    key: Key


class KeypadKeyPressEvent(KeypadEvent): ...
