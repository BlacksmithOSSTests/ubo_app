# ruff: noqa: D100, D101, D102, D103, D104, D107
from __future__ import annotations

from typing import TYPE_CHECKING, TypeAlias, TypedDict, cast

from redux import (
    BaseAction,
    InitAction,
    InitializationActionError,
    ReducerType,
    combine_reducers,
    create_store,
)

from .status_icons import reducer as status_icons_reducer

if TYPE_CHECKING:
    from .status_icons import StatusIconsState


class MainState(TypedDict):
    pass


class RootState(TypedDict):
    main: MainState
    status_icons: StatusIconsState


MainAction: TypeAlias = InitAction


def main_reducer(state: MainState | None, action: MainAction) -> MainState:
    if state is None:
        if action.type == 'INIT':
            return {}
        raise InitializationActionError

    return state


root_reducer, reducer_id = combine_reducers(
    main=main_reducer,
    status_icons=status_icons_reducer,
)
root_reducer = cast(ReducerType[RootState, BaseAction], root_reducer)


store = create_store(root_reducer)

store.dispatch(InitAction(type='INIT'))

autorun = store.autorun
dispatch = store.dispatch
subscribe = store.subscribe

__ALL__ = (autorun, dispatch, subscribe)