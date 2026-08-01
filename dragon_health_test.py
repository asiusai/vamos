#!/usr/bin/env python3
import unittest
from unittest.mock import AsyncMock, patch

import numpy as np

import dragon_health


class TestDecodedImageHealth(unittest.TestCase):
    def test_normal_image(self):
        gradient = np.linspace(0, 255, 1344, dtype=np.uint8)
        image = np.repeat(gradient[None, :, None], 760, axis=0)
        image = np.repeat(image, 3, axis=2)

        valid, metrics = dragon_health.decoded_image_health(image)

        self.assertTrue(valid)
        self.assertFalse(metrics["low_light"])

    def test_textured_low_light_image(self):
        checkerboard = np.indices((760, 1344)).sum(axis=0) % 2
        luminance = np.where(checkerboard, 14, 8).astype(np.uint8)
        image = np.repeat(luminance[:, :, None], 3, axis=2)

        valid, metrics = dragon_health.decoded_image_health(image)

        self.assertTrue(valid)
        self.assertTrue(metrics["low_light"])

    def test_blank_image(self):
        image = np.zeros((760, 1344, 3), dtype=np.uint8)

        valid, _ = dragon_health.decoded_image_health(image)

        self.assertFalse(valid)

    def test_flat_tinted_image(self):
        image = np.empty((760, 1344, 3), dtype=np.uint8)
        image[:] = (0, 20, 40)

        valid, _ = dragon_health.decoded_image_health(image)

        self.assertFalse(valid)

    def test_horizontal_stripes(self):
        luminance = np.repeat((np.arange(760) % 2 * 255)[:, None], 1344, axis=1).astype(np.uint8)
        image = np.repeat(luminance[:, :, None], 3, axis=2)

        valid, _ = dragon_health.decoded_image_health(image)

        self.assertFalse(valid)

    def test_wrong_dimensions(self):
        image = np.zeros((759, 1344, 3), dtype=np.uint8)

        valid, _ = dragon_health.decoded_image_health(image)

        self.assertFalse(valid)


class TestCameradProcessDetection(unittest.TestCase):
    @patch.object(dragon_health, "run")
    def test_prefers_v1_process(self, run):
        run.return_value = (0, "", "")

        self.assertEqual(dragon_health.active_camerad_process(), "camerad_v1")
        run.assert_called_once_with(["pgrep", "-x", "camerad_v1"], timeout=2)

    @patch.object(dragon_health, "run")
    def test_accepts_stock_process(self, run):
        run.side_effect = [(-1, "", ""), (0, "", "")]

        self.assertEqual(dragon_health.active_camerad_process(), "camerad")

    @patch.object(dragon_health, "run")
    def test_no_camera_process(self, run):
        run.return_value = (-1, "", "")

        self.assertIsNone(dragon_health.active_camerad_process())


class TestBluetoothHealth(unittest.TestCase):
    @patch.object(dragon_health, "bluez_adapter_state", new_callable=AsyncMock, return_value=(True, True))
    @patch.object(dragon_health, "run", return_value=(-1, "", ""))
    def test_bluez_adapter_without_cli_tools(self, run, bluez_adapter_state):
        self.assertTrue(dragon_health.check_bluetooth())
        bluez_adapter_state.assert_awaited_once()
        self.assertEqual(run.call_count, 2)

    @patch.object(dragon_health, "bluez_adapter_state", new_callable=AsyncMock, return_value=(False, False))
    @patch.object(dragon_health, "run", return_value=(-1, "", ""))
    def test_missing_controller_fails(self, run, bluez_adapter_state):
        self.assertFalse(dragon_health.check_bluetooth())
        bluez_adapter_state.assert_awaited_once()
        self.assertEqual(run.call_count, 2)


if __name__ == "__main__":
    unittest.main()
