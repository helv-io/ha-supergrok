"""Service validation errors. No live xAI."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry
from voluptuous import MultipleInvalid

from custom_components.grok_oauth import async_setup
from custom_components.grok_oauth.const import (
    CONF_SELECTED_MODELS,
    DOMAIN,
    SERVICE_CREATE_REALTIME_SESSION,
    SERVICE_GENERATE_CONTENT,
    SERVICE_GENERATE_IMAGE,
)


async def _register_services(hass: HomeAssistant) -> None:
    """Register the three public services without a live config entry."""
    assert await async_setup(hass, {})
    await hass.async_block_till_done()


async def test_generate_content_rejects_unknown_entry(hass: HomeAssistant) -> None:
    """generate_content raises when the config entry id is not a Grok entry."""
    await _register_services(hass)
    with pytest.raises(ServiceValidationError, match="Invalid Grok OAuth config entry"):
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
    with pytest.raises(ServiceValidationError, match="Invalid Grok OAuth config entry"):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_GENERATE_IMAGE,
            {"config_entry": "missing-entry", "prompt": "a cat"},
            blocking=True,
            return_response=True,
        )


async def test_create_realtime_session_requires_realtime(
    hass: HomeAssistant,
) -> None:
    """create_realtime_session raises when Realtime was not selected."""
    await _register_services(hass)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "access_token": "test-access-token",
            "refresh_token": "test-refresh-token",
            "expires_at": 9999999999,
            CONF_SELECTED_MODELS: ["grok-4.6"],
        },
        entry_id="grok-no-realtime",
    )
    entry.add_to_hass(hass)

    with pytest.raises(ServiceValidationError, match="Realtime is not enabled"):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_CREATE_REALTIME_SESSION,
            {"config_entry": entry.entry_id},
            blocking=True,
            return_response=True,
        )


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


async def test_create_realtime_session_rejects_short_ttl(
    hass: HomeAssistant,
) -> None:
    """expires_seconds is validated by the service schema (30–3600)."""
    await _register_services(hass)
    with pytest.raises((ServiceValidationError, MultipleInvalid)):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_CREATE_REALTIME_SESSION,
            {"config_entry": "any", "expires_seconds": 1},
            blocking=True,
            return_response=True,
        )
