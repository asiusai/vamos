from pathlib import Path
import subprocess
import tempfile
import unittest

from tools.build.check_kernel_dtb import check_reservations


class FirmwareReservationsTest(unittest.TestCase):
  def compile(self, reservation: str) -> Path:
    temporary = tempfile.TemporaryDirectory()
    self.addCleanup(temporary.cleanup)
    path = Path(temporary.name) / "board.dtb"
    source = f"""/dts-v1/;
{reservation}
/ {{
  #address-cells = <2>;
  #size-cells = <2>;
  reserved-memory {{
    #address-cells = <2>;
    #size-cells = <2>;
    ranges;
    adsp@8b800000 {{ reg = <0 0x8b800000 0 0x2800000>; no-map; }};
  }};
}};
"""
    subprocess.run(["dtc", "-I", "dts", "-O", "dtb", "-o", str(path)], input=source, text=True, check=True)
    return path

  def test_named_region_without_duplicate_reservation(self):
    check_reservations(self.compile(""))

  def test_firmware_gap_can_end_at_dsp_region(self):
    check_reservations(self.compile("/memreserve/ 0x84300000 0x7500000;"))

  def test_old_blanket_reservation_rejects_dsp_probe(self):
    with self.assertRaisesRegex(ValueError, "adsp@8b800000 overlaps"):
      check_reservations(self.compile("/memreserve/ 0x84300000 0x1bd00000;"))


if __name__ == "__main__":
  unittest.main()
