import importlib.util
from pathlib import Path
import tempfile
import unittest

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
        site / "raylib/install/lib/libraylib_headless.a",
        root / "usr/lib/libvulkan_freedreno.so",
        root / "usr/lib/libvulkan_lvp.so",
      )
      remove = (
        site / "numpy/tests/test_core.py",
        site / "sympy/core/tests/test_numbers.py",
        site / "raylib/install/lib/libgallium-26.2.1.so",
        root / "usr/lib/libvulkan_asahi.so",
        root / "usr/share/vulkan/icd.d/asahi_icd.aarch64.json",
      )
      graphics = ("libEGL.so.1", "libGLESv2.so.2", "libdrm.so.2")
      for path in (*keep, *remove, root / "ASIUS"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("preserve content")
      for name in graphics:
        (root / "usr/lib" / name).write_text("system driver")
        (site / "raylib/install/lib" / name).write_text("bundled driver")

      for _ in range(2):
        MODULE.trim(root, "unused-no-extension-in-fixture")
        for path in keep:
          self.assertEqual(path.read_text(), "preserve content")
        for path in remove:
          self.assertFalse(path.exists())
        for name in graphics:
          self.assertEqual((site / "raylib/install/lib" / name).readlink(), Path("/usr/lib") / name)

  def test_rejects_unmarked_root(self):
    with tempfile.TemporaryDirectory() as directory:
      root = Path(directory)
      site = root / "usr/local/venv/lib/python3.12/site-packages"
      site.mkdir(parents=True)
      with self.assertRaises(ValueError):
        MODULE.trim(root, "unused")


if __name__ == "__main__":
  unittest.main()
