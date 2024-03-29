# pyright: reportMissingImports=false
# ruff: noqa: D100, D101, D102, D103, D104, D105, D107
from __future__ import annotations

import asyncio
import contextlib
import uuid
from typing import TYPE_CHECKING, Any, Coroutine, TypeVar, cast

from debouncer import DebounceOptions, debounce
from ubo_gui.constants import DANGER_COLOR

from ubo_app.store import dispatch
from ubo_app.store.services.notifications import (
    Chime,
    Notification,
    NotificationDisplayType,
    NotificationsAddAction,
)
from ubo_app.store.services.wifi import (
    ConnectionState,
    GlobalWiFiState,
    WiFiConnection,
    WiFiType,
)
from ubo_app.utils import IS_RPI
from ubo_app.utils.bus_provider import get_system_bus

if TYPE_CHECKING:
    from asyncio.tasks import _FutureLike

RETRIES = 3

T = TypeVar('T')


def wait_for(task: _FutureLike[T]) -> Coroutine[Any, Any, T]:
    return asyncio.wait_for(task, timeout=10.0)


if not IS_RPI:
    import sys

    from ubo_app.utils.fake import Fake

    sys.modules['sdbus'] = Fake()
    sys.modules['sdbus_async'] = Fake()
    sys.modules['sdbus_async.networkmanager'] = Fake()
    sys.modules['sdbus_async.networkmanager.enums'] = Fake()


from sdbus_async.networkmanager import (  # noqa: E402
    AccessPoint,
    ActiveConnection,
    DeviceState,
    NetworkConnectionSettings,
    NetworkDeviceGeneric,
    NetworkDeviceWireless,
    NetworkManager,
    NetworkManagerConnectionProperties,
    NetworkManagerSettings,
)
from sdbus_async.networkmanager.enums import (  # noqa: E402
    ConnectionState as SdBusConnectionState,
)
from sdbus_async.networkmanager.enums import DeviceType  # noqa: E402


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


async def get_wifi_device_state() -> GlobalWiFiState:
    wifi_device = await get_wifi_device()
    if wifi_device is None:
        return GlobalWiFiState.UNKNOWN

    state = await wifi_device.state
    if state is DeviceState.UNKNOWN:
        return GlobalWiFiState.UNKNOWN
    if state in (
        DeviceState.DISCONNECTED,
        DeviceState.UNMANAGED,
        DeviceState.UNAVAILABLE,
        DeviceState.FAILED,
    ):
        return GlobalWiFiState.DISCONNECTED
    if state in (DeviceState.NEED_AUTH,):
        return GlobalWiFiState.NEEDS_ATTENTION
    if state in (
        DeviceState.DEACTIVATING,
        DeviceState.PREPARE,
        DeviceState.CONFIG,
        DeviceState.IP_CONFIG,
        DeviceState.IP_CHECK,
        DeviceState.SECONDARIES,
    ):
        return GlobalWiFiState.PENDING
    if state == DeviceState.ACTIVATED:
        return GlobalWiFiState.CONNECTED

    return GlobalWiFiState.UNKNOWN


@debounce(wait=0.5, options=DebounceOptions(trailing=True, time_window=2))
async def request_scan() -> None:
    wifi_device = await get_wifi_device()
    if wifi_device:
        await wait_for(wifi_device.request_scan({}))


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


async def get_active_access_point_ssid() -> str | None:
    active_access_point = await get_active_access_point()
    if not active_access_point:
        return None

    return (
        await wait_for(
            active_access_point.ssid,
        )
    ).decode('utf-8')


async def get_active_connection() -> ActiveConnection | None:
    wifi_device = await get_wifi_device()
    if wifi_device is None:
        return None

    active_connection = await wait_for(
        wifi_device.active_connection,
    )
    if not active_connection or active_connection == '/':
        return None

    return ActiveConnection(active_connection, get_system_bus())


async def get_active_connection_ssid() -> str | None:
    active_connection = await get_active_connection()
    if not active_connection:
        return None

    connection = NetworkConnectionSettings(await active_connection.connection)
    settings = await connection.get_settings()
    return settings['802-11-wireless']['ssid'][1].decode('utf-8')


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
            wifi_device._dbus.object_path,  # noqa: SLF001
            access_point._dbus.object_path,  # noqa: SLF001
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
            connection._dbus.object_path,  # noqa: SLF001
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
            wifi_device._dbus.object_path,  # noqa: SLF001
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
            await wait_for(network_connection_settings.delete())
            dispatch(
                NotificationsAddAction(
                    notification=Notification(
                        title=f'"{ssid}" Deleted',
                        content=f"""WiFi connection with ssid "{
                        ssid}" was deleted successfully""",
                        display_type=NotificationDisplayType.FLASH,
                        color=DANGER_COLOR,
                        icon='󱛅',
                        chime=Chime.DONE,
                    ),
                ),
            )


async def get_connections() -> list[WiFiConnection]:
    # It is need as this action is not atomic and the active_connection may not be
    # available when active_connection.state is queried
    for _ in range(RETRIES):
        with contextlib.suppress(Exception):
            active_connection = await get_active_connection()
            active_connection_ssid = await get_active_connection_ssid()
            saved_ssids = await get_saved_ssids()
            access_point_ssids = {
                (
                    await wait_for(
                        i.ssid,
                    )
                ).decode('utf-8'): i
                for i in await get_access_points()
            }

            active_connection_state = cast(
                SdBusConnectionState,
                await active_connection.state if active_connection else None,
            )

            state_map = {
                SdBusConnectionState.ACTIVATED: ConnectionState.CONNECTED,
                SdBusConnectionState.ACTIVATING: ConnectionState.CONNECTING,
                SdBusConnectionState.DEACTIVATED: ConnectionState.DISCONNECTED,
                SdBusConnectionState.DEACTIVATING: ConnectionState.DISCONNECTED,
                SdBusConnectionState.UNKNOWN: ConnectionState.UNKNOWN,
            }

            return [
                WiFiConnection(
                    ssid=ssid,
                    signal_strength=await wait_for(
                        access_point_ssids[ssid].strength,
                    )
                    if ssid in access_point_ssids
                    else 0,
                    state=state_map[active_connection_state]
                    if active_connection_ssid == ssid
                    else ConnectionState.DISCONNECTED,
                )
                for ssid in saved_ssids
            ]
        await asyncio.sleep(0.5)
    return []
