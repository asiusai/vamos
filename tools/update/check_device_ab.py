#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from vamos import update


def require(condition: bool, message: str) -> None:
  if not condition:
    raise update.UpdateError(message)


def normalized(control: dict[str, str | int]) -> dict[str, str | int]:
  return {key: value for key, value in control.items() if key != "source_slot"}


def validate_controls(active: str) -> tuple[dict[str, str | int], list[dict[str, str | int]]]:
  controls = update.read_boot_controls()
  require(len(controls) == 2, f"expected two readable boot-control blocks, found {len(controls)}")
  require({str(control["source_slot"]) for control in controls} == {"a", "b"},
          "boot-control blocks did not come from both ESPs")
  require(normalized(controls[0]) == normalized(controls[1]), "redundant boot-control blocks differ")

  selected = update.selected_boot_control()
  require(selected is not None, "no valid boot-control state")
  require(selected["active"] == active, "boot-control active slot does not match the running slot")
  require(selected["phase"] == "stable", f"boot-control phase is {selected['phase']}, expected stable")
  require(selected.get("pending", "") == "", "stable boot-control state has a pending slot")
  for slot in ("a", "b"):
    require(selected[f"root_{slot}"] == update.root_reference(slot),
            f"slot {slot} root reference does not match GPT")
  return selected, controls


def validate_esps() -> None:
  selector = update.BOOT_SELECTOR_SOURCE.read_bytes()
  update.verify_arm64_efi(update.BOOT_SELECTOR_SOURCE)
  for slot in ("a", "b"):
    device = update.partition_path(slot, "esp")
    update.verify_esp_contents(device)
    label = update.run(["fatlabel", str(device)]).stdout.strip()
    require(label == update.ESP_VOLUME_LABEL[slot],
            f"slot {slot} ESP label is {label!r}, expected {update.ESP_VOLUME_LABEL[slot]!r}")
    with update.mounted_esp(device, read_only=True) as esp:
      require((esp / update.ESP_PRIMARY_LOADER).read_bytes() == selector,
              f"slot {slot} primary selector differs from the installed OS")
      require((esp / update.ESP_RECOVERY_LOADER).read_bytes() == selector,
              f"slot {slot} fallback selector differs from the installed OS")


def validate_layout() -> None:
  update.verify_layout()
  update.run(["sfdisk", "--verify", str(update.DISK)], capture=False)
  for slot in ("a", "b"):
    require(update.block_size(update.partition_path(slot, "esp")) == update.ESP_SIZE,
            f"slot {slot} ESP has the wrong size")
    require(update.block_size(update.partition_path(slot, "system")) == update.SYSTEM_SIZE,
            f"slot {slot} rootfs has the wrong size")
  userdata = update.PARTLABEL_DIR / "userdata"
  require(userdata.exists() and update.block_size(userdata) > 0, "persistent userdata is missing")


def main() -> int:
  parser = argparse.ArgumentParser(description="Validate Dragon A/B storage and boot control")
  parser.add_argument("--write-check", action="store_true",
                      help="rewrite the current stable state to prove both ESPs are writable")
  args = parser.parse_args()

  require(os.geteuid() == 0, "A/B health check must run as root")
  active = update.current_slot()
  validate_layout()
  validate_esps()
  selected, _ = validate_controls(active)

  state = update.load_state()
  require(state.get("state") not in {"writing", "ready", "booting"},
          f"update is still in unsafe state {state.get('state')!r}")

  if args.write_check:
    previous_generation = int(selected["generation"])
    generation = update.set_boot_control(active)
    require(generation > previous_generation, "stable-state write did not advance the generation")
    selected, _ = validate_controls(active)
    require(int(selected["generation"]) == generation, "new generation was not persisted redundantly")
    os.sync()

  print(json.dumps({
    "active_slot": active,
    "generation": selected["generation"],
    "phase": selected["phase"],
    "write_checked": args.write_check,
    "status": "passed",
  }, indent=2, sort_keys=True))
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
