"""Tests for Daikin climate commands."""

from aiohttp import ServerDisconnectedError
from pydaikin.daikin_base import Appliance
from pydaikin.daikin_brp069 import DaikinBRP069
from pydaikin.daikin_brp084 import DaikinBRP084
from pydaikin.exceptions import DaikinException
import pytest
from pytest_homeassistant.common import MockConfigEntry

from homeassistant.components.climate import (
    DOMAIN as CLIMATE_DOMAIN,
    SERVICE_SET_HVAC_MODE,
    HVACMode,
)
from homeassistant.components.daikin.const import DOMAIN, KEY_MAC
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.const import (
    ATTR_ENTITY_ID,
    CONF_HOST,
    SERVICE_TURN_OFF,
    STATE_OFF,
    STATE_ON,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from .conftest import ZoneDevice


async def _async_setup(hass: HomeAssistant, zone_device: ZoneDevice) -> None:
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=zone_device.mac,
        data={CONF_HOST: "127.0.0.1", KEY_MAC: zone_device.mac},
    )
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (ServerDisconnectedError(), "Server disconnected"),
        (DaikinException("rejected"), "rejected"),
        (TimeoutError(), "TimeoutError"),
    ],
)
async def test_set_hvac_mode_raises_a_clear_error(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    zone_device: ZoneDevice,
    error: Exception,
    message: str,
) -> None:
    """A command the device doesn't take raises a translated error, not a crash."""
    await _async_setup(hass, zone_device)
    entity_id = entity_registry.async_get_entity_id(
        CLIMATE_DOMAIN, DOMAIN, zone_device.mac
    )
    assert entity_id is not None
    zone_device.set.side_effect = error

    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            CLIMATE_DOMAIN,
            SERVICE_SET_HVAC_MODE,
            {ATTR_ENTITY_ID: entity_id, "hvac_mode": HVACMode.HEAT},
            blocking=True,
        )

    assert err.value.translation_domain == DOMAIN
    assert err.value.translation_key == "error_communicating"
    assert err.value.translation_placeholders == {"error": message}


async def test_switch_command_raises_a_clear_error(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    zone_device: ZoneDevice,
) -> None:
    """Switches report a failed command the same way."""
    await _async_setup(hass, zone_device)
    entity_id = entity_registry.async_get_entity_id(
        SWITCH_DOMAIN, DOMAIN, f"{zone_device.mac}-zone0"
    )
    assert entity_id is not None
    zone_device.set_zone.side_effect = ServerDisconnectedError()

    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            SWITCH_DOMAIN,
            SERVICE_TURN_OFF,
            {ATTR_ENTITY_ID: entity_id},
            blocking=True,
        )

    assert err.value.translation_key == "error_communicating"


@pytest.mark.parametrize(
    ("device_class", "refresh_count"),
    [
        pytest.param(DaikinBRP084, 0, id="brp084"),
        pytest.param(DaikinBRP069, 1, id="brp069"),
    ],
)
async def test_turn_off_updates_climate_and_switch(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    zone_device: ZoneDevice,
    device_class: type[Appliance],
    refresh_count: int,
) -> None:
    """Turning off the unit publishes its state to both entities."""
    await _async_setup(hass, zone_device)
    zone_device.mock_add_spec(device_class)
    climate_id = entity_registry.async_get_entity_id(
        CLIMATE_DOMAIN, DOMAIN, zone_device.mac
    )
    switch_id = entity_registry.async_get_entity_id(
        SWITCH_DOMAIN, DOMAIN, f"{zone_device.mac}-toggle"
    )
    assert climate_id is not None
    assert switch_id is not None
    assert hass.states.is_state(climate_id, HVACMode.HEAT)
    assert hass.states.is_state(switch_id, STATE_ON)
    zone_device.update_status.reset_mock()

    await hass.services.async_call(
        CLIMATE_DOMAIN, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: climate_id}, blocking=True
    )

    zone_device.set.assert_awaited_once_with({"mode": "off"})
    assert zone_device.update_status.await_count == refresh_count
    assert hass.states.is_state(climate_id, HVACMode.OFF)
    assert hass.states.is_state(switch_id, STATE_OFF)
