"""The local camera picture, the right way up.

A printer serves its camera as the sensor sees it. On a Photon P1 Max that is
sideways, and nothing in the stream says so. The library knows the answer per
model; the owner can overrule it in the options; Home Assistant does the
turning, so nothing is re-encoded here.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.camera import Camera
from homeassistant.components.stream import Orientation

from custom_components.anycubic_cloud.camera import (
    CAMERA_TYPES,
    AnycubicCamera,
    orientation_for_rotation,
)
from custom_components.anycubic_cloud.const import (
    CAMERA_ROTATION_AUTO,
    CAMERA_ROTATION_OPTIONS,
    CONF_CAMERA_ROTATION,
)
from custom_components.anycubic_cloud.coordinator import (
    AnycubicCloudDataUpdateCoordinator,
)


def _fake_coordinator(options: dict, printer) -> MagicMock:
    """Only what camera_rotation reads: the options and the printer."""
    coordinator = MagicMock()
    coordinator.entry.options = options
    coordinator.get_printer_for_id.return_value = printer

    return coordinator


def _rotation(options: dict, printer) -> int:
    return AnycubicCloudDataUpdateCoordinator.camera_rotation(
        _fake_coordinator(options, printer), 1
    )


class TestWhichRotationApplies:
    def test_with_nothing_chosen_the_model_s_own_is_used(self) -> None:
        assert _rotation({}, SimpleNamespace(camera_rotation=270)) == 270

    def test_automatic_means_the_model_s_own(self) -> None:
        options = {CONF_CAMERA_ROTATION: CAMERA_ROTATION_AUTO}

        assert _rotation(options, SimpleNamespace(camera_rotation=270)) == 270

    @pytest.mark.parametrize("chosen", ["0", "90", "180", "270"])
    def test_a_chosen_rotation_wins_over_the_model(self, chosen: str) -> None:
        options = {CONF_CAMERA_ROTATION: chosen}

        assert _rotation(options, SimpleNamespace(camera_rotation=270)) == int(chosen)

    def test_a_library_that_does_not_know_about_rotation_means_upright(self) -> None:
        assert _rotation({}, SimpleNamespace()) == 0

    def test_a_printer_that_is_not_loaded_yet_means_upright(self) -> None:
        assert _rotation({}, None) == 0

    def test_a_damaged_option_means_upright_rather_than_an_error(self) -> None:
        assert _rotation({CONF_CAMERA_ROTATION: "sideways"}, None) == 0

    def test_every_offered_option_is_understood(self) -> None:
        for option in CAMERA_ROTATION_OPTIONS:
            _rotation({CONF_CAMERA_ROTATION: option}, SimpleNamespace(camera_rotation=90))


class TestWhatHomeAssistantIsTold:
    @pytest.mark.parametrize("degrees, orientation", [
        (0, Orientation.NO_TRANSFORM),
        (90, Orientation.ROTATE_RIGHT),
        (180, Orientation.ROTATE_180),
        (270, Orientation.ROTATE_LEFT),
        (450, Orientation.ROTATE_RIGHT),
    ])
    def test_clockwise_degrees_become_an_orientation(self, degrees, orientation) -> None:
        assert orientation_for_rotation(degrees) is orientation

    def test_anything_else_is_left_upright(self) -> None:
        assert orientation_for_rotation(45) is Orientation.NO_TRANSFORM


def _camera(rotation: int) -> AnycubicCamera:
    with patch.object(AnycubicCamera, "__init__", lambda self, *a, **k: None):
        camera = AnycubicCamera()

    camera.coordinator = MagicMock()
    camera.coordinator.camera_rotation.return_value = rotation
    camera._printer_id = 1
    camera.entity_description = CAMERA_TYPES[0]
    camera._applied_orientation = None
    camera._opening_hls_stream = False
    camera.hass = MagicMock()
    camera.hass.config.components = set()

    return camera


def _stream(orientation: Orientation) -> SimpleNamespace:
    return SimpleNamespace(
        dynamic_stream_settings=SimpleNamespace(orientation=orientation)
    )


class TestTheStreamIsOpenedTheRightWayUp:
    async def test_the_rotation_is_handed_to_home_assistant(self) -> None:
        camera = _camera(270)
        stream = _stream(Orientation.NO_TRANSFORM)

        with patch.object(Camera, "async_create_stream", AsyncMock(return_value=stream)):
            await camera.async_create_stream()

        assert stream.dynamic_stream_settings.orientation is Orientation.ROTATE_LEFT

    async def test_an_orientation_set_in_the_entity_settings_is_left_alone(self) -> None:
        camera = _camera(270)
        stream = _stream(Orientation.ROTATE_180)

        with patch.object(Camera, "async_create_stream", AsyncMock(return_value=stream)):
            await camera.async_create_stream()

        assert stream.dynamic_stream_settings.orientation is Orientation.ROTATE_180

    async def test_what_it_set_itself_follows_a_later_change(self) -> None:
        camera = _camera(270)
        stream = _stream(Orientation.NO_TRANSFORM)

        with patch.object(Camera, "async_create_stream", AsyncMock(return_value=stream)):
            await camera.async_create_stream()
            camera.coordinator.camera_rotation.return_value = 90
            await camera.async_create_stream()

        assert stream.dynamic_stream_settings.orientation is Orientation.ROTATE_RIGHT

    async def test_no_stream_is_no_stream(self) -> None:
        camera = _camera(270)

        with patch.object(Camera, "async_create_stream", AsyncMock(return_value=None)):
            assert await camera.async_create_stream() is None


class TestWhereGo2rtcDoesTheShowing:
    """With go2rtc set up, live view and stills come from it, not from HLS."""

    URL = "http://10.0.66.40:18088/flv"

    def test_go2rtc_is_asked_to_turn_the_picture(self) -> None:
        camera = _camera(270)
        camera.hass.config.components = {"go2rtc"}

        assert camera._turned(self.URL) == f"ffmpeg:{self.URL}#video=h264#rotate=270"

    def test_an_upright_picture_is_not_re_encoded(self) -> None:
        camera = _camera(0)
        camera.hass.config.components = {"go2rtc"}

        assert camera._turned(self.URL) == self.URL

    def test_without_go2rtc_the_address_goes_out_as_it_is(self) -> None:
        camera = _camera(270)

        assert camera._turned(self.URL) == self.URL

    async def test_home_assistant_s_own_reader_gets_the_plain_address(self) -> None:
        camera = _camera(270)
        camera.hass.config.components = {"go2rtc"}
        seen = []

        async def create_stream(self):
            seen.append(camera._turned(TestWhereGo2rtcDoesTheShowing.URL))
            return _stream(Orientation.NO_TRANSFORM)

        with patch.object(Camera, "async_create_stream", create_stream):
            await camera.async_create_stream()

        assert seen == [self.URL]
        assert camera._opening_hls_stream is False


class TestStartingTheCapture:
    async def test_it_goes_out_in_the_full_command_envelope(self) -> None:
        """A Photon P1 Max ignores a bare message and never opens its stream."""
        coordinator = MagicMock()
        coordinator._lan_client.is_connected = True

        await AnycubicCloudDataUpdateCoordinator.async_start_camera(coordinator, 1)

        coordinator._lan_client.publish_command.assert_called_once_with(
            "video", "startCapture", {}
        )
        coordinator._lan_client.publish.assert_not_called()
