import importlib.util
import tempfile
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("trim", Path(__file__).resolve().parents[1] / "userspace/trim.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class TrimTest(unittest.TestCase):
  def test_keeps_runtime_and_build_files_on_repeated_runs(self):
    with tempfile.TemporaryDirectory() as directory:
      root = Path(directory)
      site = root / "usr/local/venv/lib/python3.12/site-packages"
      keep = (
        site / "numpy/testing/__init__.py",
        site / "sympy/testing/__init__.py",
        site / "other/tests/runtime_data",
        site / "raylib/install/include/raylib.h",
        site / "raylib/install/lib/libraylib_comma.a",
        site / "raylib/install/lib/libraylib_desktop.a",
        site / "raylib/install/lib/libraylib_headless.a",
        root / "usr/lib/libvulkan_freedreno.so",
        root / "usr/lib/libvulkan_lvp.so",
      )
      remove = (
        site / "numpy/tests/test_core.py",
        site / "sympy/core/tests/test_numbers.py",
        root / "usr/lib/libvulkan_asahi.so",
        root / "usr/share/vulkan/icd.d/asahi_icd.aarch64.json",
      )
      graphics = ("libEGL.so.1", "libGLESv2.so.2", "libdrm.so.2")
      for path in (*keep, *remove, root / "ASIUS"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("preserve content")
      for name in graphics:
        (root / "usr/lib" / f"{name}.0").write_text("system driver")
        (root / "usr/lib" / name).symlink_to(f"{name}.0")

      for _ in range(2):
        MODULE.trim(root, "unused-no-extension-in-fixture")
        for path in keep:
          self.assertEqual(path.read_text(), "preserve content")
        for path in remove:
          self.assertFalse(path.exists())
        for name in graphics:
          self.assertEqual((root / "usr/lib" / name).read_text(), "system driver")
        self.assertEqual(sorted(path.name for path in (site / "raylib/install/lib").iterdir()),
                         ["libraylib_comma.a", "libraylib_desktop.a", "libraylib_headless.a"])

      (root / "usr/lib/libEGL.so.1.0").unlink()
      with self.assertRaisesRegex(ValueError, "missing graphics runtime: libEGL.so.1"):
        MODULE.trim(root, "unused-no-extension-in-fixture")

  def test_rejects_unmarked_root(self):
    with tempfile.TemporaryDirectory() as directory:
      root = Path(directory)
      site = root / "usr/local/venv/lib/python3.12/site-packages"
      site.mkdir(parents=True)
      with self.assertRaises(ValueError):
        MODULE.trim(root, "unused")


if __name__ == "__main__":
  unittest.main()
