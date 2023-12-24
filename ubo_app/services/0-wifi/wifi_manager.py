# pyright: reportMissingImports=false
# ruff: noqa: D100, D101, D102, D103, D104, D105, D107
from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from threading import current_thread
from types import ModuleType
from typing import (
    TYPE_CHECKING,
    Any,
    Awaitable,
    Callable,
    Coroutine,
    Generator,
    Iterator,
    TypeVar,
)

from ubo_app.logging import logger
from ubo_app.store.wifi import WiFiConnection, WiFiType
from ubo_app.utils.async_ import create_task

if TYPE_CHECKING:
    from asyncio.tasks import _FutureLike


T = TypeVar('T')


def wait_for(task: _FutureLike[T]) -> Coroutine[Any, Any, T]:
    return asyncio.wait_for(task, timeout=10.0)


IS_RPI = Path('/etc/rpi-issue').exists()
if not IS_RPI:
    import sys

    class Fake(ModuleType):
        def __init__(self: Fake) -> None:
            super().__init__('')

        def __getattr__(self: Fake, attr: str) -> Fake | str:
            logger.verbose(
                'Accessing fake attribute of a `Fake` instance',
                extra={'attr': attr},
            )
            if attr in ['__file__']:
                return ''
            return Fake()

        def __call__(self: Fake, *args: object, **kwargs: dict[str, Any]) -> Fake:
            logger.verbose(
                'Calling a `Fake` instance',
                extra={'args_': args, 'kwargs': kwargs},
            )
            return Fake()

        def __await__(self: Fake) -> Generator[Fake | None, Any, Any]:
            yield None
            return Fake()

        def __iter__(self: Fake) -> Iterator[Fake]:
            return iter([Fake()])

    sys.modules['sdbus'] = Fake()
    sys.modules['sdbus_async'] = Fake()
    sys.modules['sdbus_async.networkmanager'] = Fake()
    sys.modules['sdbus_async.networkmanager.enums'] = Fake()


from sdbus import SdBus, sd_bus_open_system, set_default_bus  # noqa: E402
from sdbus_async.networkmanager import (  # noqa: E402
    AccessPoint,
    NetworkConnectionSettings,
    NetworkDeviceGeneric,
    NetworkDeviceWireless,
    NetworkManager,
    NetworkManagerConnectionProperties,
    NetworkManagerSettings,
)
from sdbus_async.networkmanager.enums import DeviceType  # noqa: E402

system_buses = {}


def get_system_bus() -> SdBus:
    thread = current_thread()
    if thread not in system_buses:
        system_buses[thread] = sd_bus_open_system()
        set_default_bus(system_buses[thread])
    return system_buses[thread]


async def get_wifi_device() -> NetworkDeviceWireless | None:
    network_manager = NetworkManager(get_system_bus())
    devices_paths = await wait_for(
        network_manager.get_devices(),
    )
    for device_path in devices_paths:
        generic_device = NetworkDeviceGeneric(device_path, get_system_bus())
        if (
            await wait_for(
                generic_device.device_type,
            )
            == DeviceType.WIFI
        ):
            return NetworkDeviceWireless(device_path, get_system_bus())
    return None


async def subscribe_to_wifi_device(event_handler: Callable[[object], Any]) -> None:
    while True:
        wifi_device = await get_wifi_device()
        if not wifi_device:
            continue

        async for event in wifi_device.properties_changed:
            result = event_handler(event)
            if isinstance(result, Awaitable):
                create_task(result)


async def request_scan() -> None:
    wifi_device = await get_wifi_device()
    if wifi_device:
        await wait_for(
            wifi_device.request_scan({}),
        )


async def get_access_points() -> list[AccessPoint]:
    wifi_device = await get_wifi_device()
    if wifi_device is None:
        return []

    access_points = await wait_for(
        wifi_device.access_points,
    )
    return [
        AccessPoint(access_point_path, get_system_bus())
        for access_point_path in access_points
    ]


async def get_active_access_point() -> AccessPoint | None:
    wifi_device = await get_wifi_device()
    if wifi_device is None:
        return None

    active_access_point = await wait_for(
        wifi_device.active_access_point,
    )
    if not active_access_point or active_access_point == '/':
        return None

    return AccessPoint(active_access_point, get_system_bus())


async def get_saved_ssids() -> list[str]:
    network_manager_settings = NetworkManagerSettings(get_system_bus())
    connections = [
        NetworkConnectionSettings(i, get_system_bus())
        for i in await wait_for(
            network_manager_settings.connections,
        )
    ]
    connections_settings = [
        await wait_for(
            i.get_settings(),
        )
        for i in connections
    ]
    return [
        settings['802-11-wireless']['ssid'][1].decode('utf-8')
        for settings in connections_settings
        if '802-11-wireless' in settings
    ]


async def get_active_ssid() -> str | None:
    active_access_point = await get_active_access_point()
    if not active_access_point:
        return None

    return (
        await wait_for(
            active_access_point.ssid,
        )
    ).decode('utf-8')


async def add_wireless_connection(
    ssid: str,
    password: str,
    type: WiFiType,
    *,
    hidden: bool | None = False,
) -> None:
    wifi_device = await get_wifi_device()
    if not wifi_device:
        return

    access_points = [
        (
            access_point,
            await wait_for(
                access_point.ssid,
            ),
        )
        for access_point in await get_access_points()
    ]
    access_point = next(
        (
            access_point
            for access_point, ssid_ in access_points
            if ssid_.decode('utf8') == ssid
        ),
        None,
    )

    if not access_point:
        return

    if type == WiFiType.nopass:
        security = {
            'key-mgmt': ('s', 'none'),
            'auth-alg': ('s', 'open'),
        }
    elif type == WiFiType.WEP:
        security = {
            'key-mgmt': ('s', 'none'),
            'auth-alg': ('s', 'open'),
            'psk': ('s', password),
        }
    elif type in (WiFiType.WPA, WiFiType.WPA2):
        security = {
            'key-mgmt': ('s', 'wpa-psk'),
            'auth-alg': ('s', 'open'),
            'psk': ('s', password),
        }

    properties: NetworkManagerConnectionProperties = {
        'connection': {
            'id': ('s', ssid),
            'uuid': ('s', str(uuid.uuid4())),
            'type': ('s', '802-11-wireless'),
            'autoconnect': ('b', True),
        },
        '802-11-wireless': {
            'mode': ('s', 'infrastructure'),
            'security': ('s', '802-11-wireless-security'),
            'ssid': ('ay', ssid.encode('utf-8')),
            'hidden': ('b', hidden),
        },
        '802-11-wireless-security': security,
        'ipv4': {'method': ('s', 'auto')},
        'ipv6': {'method': ('s', 'auto')},
    }

    network_manager = NetworkManager(get_system_bus())
    await wait_for(
        network_manager.add_and_activate_connection(
            properties,
            wifi_device._remote_object_path,  # noqa: SLF001
            access_point._remote_object_path,  # noqa: SLF001
        ),
    )


async def connect_wireless_connection(ssid: str) -> None:
    wifi_device = await get_wifi_device()

    if not wifi_device:
        return

    network_manager_settings = NetworkManagerSettings(get_system_bus())
    connections = [
        NetworkConnectionSettings(path, get_system_bus())
        for path in await wait_for(
            network_manager_settings.connections,
        )
    ]
    connections_settings = [
        (
            await wait_for(
                connection.get_settings(),
            ),
            connection._remote_object_path,  # noqa: SLF001
        )
        for connection in connections
    ]
    desired_connection = next(
        (
            path
            for settings, path in connections_settings
            if '802-11-wireless' in settings
            and settings['802-11-wireless']['ssid'][1].decode('utf-8') == ssid
        ),
        None,
    )

    if not desired_connection:
        return

    network_manager = NetworkManager(get_system_bus())
    await wait_for(
        network_manager.activate_connection(
            desired_connection,
            wifi_device._remote_object_path,  # noqa: SLF001
        ),
    )


async def disconnect_wireless_connection() -> None:
    wifi_device = await get_wifi_device()
    if not wifi_device:
        return

    network_manager = NetworkManager(get_system_bus())
    await wait_for(
        network_manager.deactivate_connection(
            await wait_for(
                wifi_device.active_connection,
            ),
        ),
    )


async def forget_wireless_connection(ssid: str) -> None:
    network_manager_settings = NetworkManagerSettings(get_system_bus())

    for connection_path in await wait_for(
        network_manager_settings.connections,
    ):
        network_connection_settings = NetworkConnectionSettings(
            connection_path,
            get_system_bus(),
        )
        settings = await wait_for(
            network_connection_settings.get_settings(),
        )
        if (
            '802-11-wireless' in settings
            and settings['802-11-wireless']['ssid'][1].decode('utf-8') == ssid
        ):
            await wait_for(
                network_connection_settings.delete(),
            )


async def get_connections() -> list[WiFiConnection]:
    active_ssid = await get_active_ssid()
    saved_ssids = await get_saved_ssids()
    access_point_ssids = {
        (
            await wait_for(
                i.ssid,
            )
        ).decode('utf-8'): i
        for i in await get_access_points()
    }

    return [
        WiFiConnection(
            ssid=ssid,
            signal_strength=await wait_for(
                access_point_ssids[ssid].strength,
            )
            if ssid in access_point_ssids
            else 0,
            is_active=active_ssid == ssid,
        )
        for ssid in saved_ssids
    ]
