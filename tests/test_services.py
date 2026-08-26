"""Service validation errors. No live xAI."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from voluptuous import MultipleInvalid

from custom_components.supergrok import async_setup
from custom_components.supergrok.const import (
    DOMAIN,
    SERVICE_CREATE_REALTIME_SESSION,
    SERVICE_GENERATE_CONTENT,
    SERVICE_GENERATE_IMAGE,
)


async def _register_services(hass: HomeAssistant) -> None:
    """Register the public services without a live config entry."""
    assert await async_setup(hass, {})
    await hass.async_block_till_done()


async def test_generate_content_rejects_unknown_entry(hass: HomeAssistant) -> None:
    """generate_content raises when the config entry id is not a Grok entry."""
    await _register_services(hass)
    with pytest.raises(ServiceValidationError, match="Invalid SuperGrok OAuth config entry"):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_GENERATE_CONTENT,
            {"config_entry": "missing-entry", "prompt": "hello"},
            blocking=True,
            return_response=True,
        )


async def test_generate_image_rejects_unknown_entry(hass: HomeAssistant) -> None:
    """generate_image raises when the config entry id is not a Grok entry."""
    await _register_services(hass)
    with pytest.raises(ServiceValidationError, match="Invalid SuperGrok OAuth config entry"):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_GENERATE_IMAGE,
            {"config_entry": "missing-entry", "prompt": "a cat"},
            blocking=True,
            return_response=True,
        )


async def test_create_realtime_session_is_not_registered(
    hass: HomeAssistant,
) -> None:
    """Realtime is withheld; create_realtime_session is not a live service."""
    await _register_services(hass)
    assert not hass.services.has_service(DOMAIN, SERVICE_CREATE_REALTIME_SESSION)
    assert hass.services.has_service(DOMAIN, SERVICE_GENERATE_CONTENT)
    assert hass.services.has_service(DOMAIN, SERVICE_GENERATE_IMAGE)


async def test_generate_image_rejects_invalid_n(hass: HomeAssistant) -> None:
    """n is validated by the service schema (1–8)."""
    await _register_services(hass)
    with pytest.raises((ServiceValidationError, MultipleInvalid)):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_GENERATE_IMAGE,
            {"config_entry": "any", "prompt": "a cat", "n": 99},
            blocking=True,
            return_response=True,
        )


