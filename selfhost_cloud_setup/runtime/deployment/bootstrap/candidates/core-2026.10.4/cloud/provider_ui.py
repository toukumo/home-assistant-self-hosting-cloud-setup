"""Serve the selected-feature UI through the pinned native Cloud menu."""
import asyncio
import hashlib
import os
import stat
from pathlib import Path
from homeassistant.components import frontend, websocket_api
from homeassistant.components.http import StaticPathConfig
from .provider_recovery import PasswordRecoveryView
from .service_core_public_status import read_file


def _frontend_prefix(directory):
    """Give the complete relative-import graph a content-bound URL identity."""
    digest = hashlib.sha256()
    for name in ('cloud-route-adapter.js', 'selfhost-dashboard.js', 'cloud-i18n.js'):
        fd = os.open(Path(directory) / name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOCTTY)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ValueError('Expected regular public frontend asset')
            with os.fdopen(fd, 'rb', closefd=False) as stream:
                raw = stream.read()
        finally:
            os.close(fd)
        digest.update(name.encode() + b'\0' + len(raw).to_bytes(8, 'big') + raw)
    return '/selfhost-cloud/assets/' + digest.hexdigest()


@websocket_api.require_admin
@websocket_api.websocket_command({'type': 'cloud/selfhost/maintenance/status'})
@websocket_api.async_response
async def websocket_maintenance_status(hass, connection, msg):
    """Expose only fresh public stored evidence to an existing administrator."""
    result = await hass.async_add_executor_job(
        read_file, hass.config.path('.selfhost-cloud-updater-status.json'))
    connection.send_result(msg['id'], result)


@websocket_api.require_admin
@websocket_api.websocket_command({'type': 'cloud/selfhost/voice/status'})
@websocket_api.async_response
async def websocket_voice_status(hass, connection, msg):
    """Read server capabilities without changing voice preferences."""
    result = {'stt': None, 'tts': None}
    cloud = hass.data.get('cloud')
    if cloud is not None and cloud.is_logged_in:
        try:
            async with asyncio.timeout(10):
                services = await cloud.account.services()
            for name in result:
                value = services.get(name, {}).get('available')
                if type(value) is bool:
                    result[name] = value
        except Exception:
            pass  # Unverified capabilities must never be advertised as available.
    connection.send_result(msg['id'], result)


async def async_setup(hass):
    websocket_api.async_register_command(hass, websocket_maintenance_status)
    websocket_api.async_register_command(hass, websocket_voice_status)
    hass.http.register_view(PasswordRecoveryView)
    frontend.add_extra_js_url(hass, '/selfhost-cloud/password-recovery-ui.js?v=3')
    directory = Path(__file__).parent
    prefix = await hass.async_add_executor_job(_frontend_prefix, directory)
    url = prefix + '/cloud-route-adapter.js'
    await hass.http.async_register_static_paths([
        StaticPathConfig('/selfhost-cloud/cloud-i18n.js', str(Path(__file__).with_name('cloud-i18n.js')), False),
        StaticPathConfig('/selfhost-cloud/cloud-route-adapter.js', str(Path(__file__).with_name('cloud-route-adapter.js')), False),
        StaticPathConfig('/selfhost-cloud/selfhost-dashboard.js', str(Path(__file__).with_name('selfhost-dashboard.js')), False),
        StaticPathConfig('/selfhost-cloud/password-recovery-ui.js', str(Path(__file__).with_name('password-recovery-ui.js')), False),
        StaticPathConfig('/selfhost-cloud/provider-icon.png', str(Path(__file__).with_name('provider-icon.png')), True),
    ] + [StaticPathConfig(prefix + '/' + name, str(directory / name), False)
         for name in ('cloud-i18n.js', 'cloud-route-adapter.js', 'selfhost-dashboard.js')])
    frontend.add_extra_js_url(hass, url)
