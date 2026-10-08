"""Service validation errors. No live xAI."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry
from voluptuous import MultipleInvalid

from custom_components.supergrok import async_setup
from custom_components.supergrok.const import (
    CONF_SELECTED_MODELS,
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




def _png() -> bytes:
    return bytes.fromhex(
        "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
        "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"
    )


@pytest.fixture
async def image_setup(hass: HomeAssistant, tmp_path):
    """A loaded entry with a mock client and one allowed folder."""
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    hass.config.allowlist_external_dirs = {str(allowed)}
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_SELECTED_MODELS: ["grok-4.6"]})
    entry.add_to_hass(hass)
    client = MagicMock()
    client.chat = AsyncMock(return_value=MagicMock(text="A cat."))
    entry.runtime_data = client
    await _register_services(hass)
    return entry, client, allowed, tmp_path


async def _describe(hass: HomeAssistant, entry, path) -> dict:
    return await hass.services.async_call(
        DOMAIN,
        SERVICE_GENERATE_CONTENT,
        {"config_entry": entry.entry_id, "prompt": "What is this?", "image_filename": [str(path)]},
        blocking=True,
        return_response=True,
    )


async def test_generate_content_sends_allowed_image(hass: HomeAssistant, image_setup) -> None:
    """An image in an allowed folder is attached with its type."""
    entry, client, allowed, _ = image_setup
    image = allowed / "snapshot.png"
    image.write_bytes(_png())

    result = await _describe(hass, entry, image)

    assert result["text"] == "A cat."
    content = client.chat.await_args.kwargs["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "What is this?"}
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


async def test_generate_content_rejects_path_outside_allowlist(
    hass: HomeAssistant, image_setup
) -> None:
    """A file outside allowlist_external_dirs is never read or sent."""
    entry, client, _, tmp_path = image_setup
    secret = tmp_path / "secrets.png"
    secret.write_bytes(_png())

    with pytest.raises(ServiceValidationError) as err:
        await _describe(hass, entry, secret)

    assert err.value.translation_key == "image_not_allowed"
    assert err.value.translation_placeholders == {"filename": str(secret)}
    client.chat.assert_not_called()


async def test_generate_content_rejects_traversal(hass: HomeAssistant, image_setup) -> None:
    """A path that climbs out of an allowed folder is rejected."""
    entry, client, allowed, tmp_path = image_setup
    (tmp_path / "outside.png").write_bytes(_png())

    with pytest.raises(ServiceValidationError) as err:
        await _describe(hass, entry, allowed / ".." / "outside.png")

    assert err.value.translation_key == "image_not_allowed"
    client.chat.assert_not_called()


async def test_generate_content_rejects_missing_file(hass: HomeAssistant, image_setup) -> None:
    """A missing file in an allowed folder gets its own error."""
    entry, client, allowed, _ = image_setup

    with pytest.raises(ServiceValidationError) as err:
        await _describe(hass, entry, allowed / "missing.jpg")

    assert err.value.translation_key == "image_not_found"
    client.chat.assert_not_called()


async def test_generate_content_rejects_non_image(hass: HomeAssistant, image_setup) -> None:
    """Only JPEG, PNG, WebP and GIF files are sent."""
    entry, client, allowed, _ = image_setup
    notes = allowed / "notes.txt"
    notes.write_text("not an image", encoding="utf-8")

    with pytest.raises(ServiceValidationError) as err:
        await _describe(hass, entry, notes)

    assert err.value.translation_key == "image_not_supported"
    client.chat.assert_not_called()
