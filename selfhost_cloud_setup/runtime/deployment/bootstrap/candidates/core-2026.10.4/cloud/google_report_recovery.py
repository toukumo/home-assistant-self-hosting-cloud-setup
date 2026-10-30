"""Recover reporting on exposure changes without patching native Google source."""
from homeassistant.components.homeassistant.exposed_entities import async_listen_entity_updates
from homeassistant.core import callback
from homeassistant.helpers import start


def restart_reporting(config):
    if config.should_report_state:
        config.async_disable_report_state()
        config.async_enable_report_state()


def install(hass, cloud):
    unsubscribers=[]
    task=None
    active=True

    async def recover():
        config=await cloud.client.get_google_config()
        if active:
            restart_reporting(config)

    @callback
    def exposed_changed():
        nonlocal task
        if not active or not cloud.is_logged_in or not cloud.client.prefs.google_enabled:
            return
        if task is None or task.done():
            task=hass.async_create_task(recover())

    async def started(hass):
        if active:
            unsubscribers.append(async_listen_entity_updates(hass, 'cloud.google_assistant', exposed_changed))

    unsubscribers.append(start.async_at_started(hass, started))

    @callback
    def remove():
        nonlocal active
        active=False
        for unsubscribe in unsubscribers:
            unsubscribe()
        unsubscribers.clear()
        if task is not None:
            task.cancel()

    return remove
