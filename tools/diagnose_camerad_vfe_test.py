#!/usr/bin/env python3
"""Focused tests for diagnose_camerad_vfe.py log parsing."""

from __future__ import annotations

import unittest

import diagnose_camerad_vfe as diagnose


class DiagnoseCameradVfeTest(unittest.TestCase):
  def test_parse_ae_samples_for_selected_camera(self) -> None:
    log_text = "\n".join([
      "cam 0: OS04 AE grey=0.4102 target=0.4200 rgb_clip=0.0820 cur_ev=36.00 desired_ev=35.70 unclipped_ev=36.40 exp 25->24 gain_idx 0->0 gain 1.000",
      "cam 1: OS04 AE grey=0.4023 target=0.3800 rgb_clip=0.0947 cur_ev=14.00 desired_ev=13.90 unclipped_ev=13.80 exp 14->13 gain_idx 0->0 gain 1.000",
    ])

    samples = diagnose.parse_ae_samples(log_text, "cam2")

    self.assertEqual(1, len(samples))
    self.assertEqual(0.4023, samples[0]["grey"])
    self.assertEqual(13, samples[0]["exp"])
    self.assertEqual(1.0, samples[0]["gain_factor"])

  def test_parse_awb_samples_for_selected_camera(self) -> None:
    log_text = "\n".join([
      "cam 0: OS04 AWB stable U=127 V=126 samples=3784 neutral=3784 blue=0x116 red=0x11a",
      "cam 1: OS04 AWB U=127 V=126 samples=3917 neutral=3917 blue=0x12c red=0x130",
    ])

    samples = diagnose.parse_awb_samples(log_text, "cam3")

    self.assertEqual(1, len(samples))
    self.assertTrue(samples[0]["stable"])
    self.assertEqual(127, samples[0]["u"])
    self.assertEqual(0x116, samples[0]["blue"])


if __name__ == "__main__":
  unittest.main()
