"""Program a prepared factory manifest through one verified Firehose session."""

import argparse
import json
from pathlib import Path

from tools.flash.edl_normal import EdlDisk, require
from tools.flash.prepare_rawprogram import operations_for_payload, prepare_payload
from tools.flash.persist import initialize_persist


def prepare_operations(manifest_path: Path, output: Path, include_openpilot: bool) -> tuple[int, list]:
    manifest = json.loads(manifest_path.read_text())
    operations = []
    for payload in ("base", "openpilot") if include_openpilot else ("base",):
        directory = output / payload
        prepare_payload(manifest_path, directory, payload)
        index = 0
        for operation in operations_for_payload(manifest, payload):
            path = None
            if operation["operation"] == "program":
                path = directory / f"chunk-{index:05d}.img"
                index += 1
            operations.append((operation, path))
    return manifest["sector_size"], operations


def flash_operations(disk: EdlDisk, sector_size: int, operations: list, persist: dict | None = None) -> None:
    require(disk.sector_size == sector_size, "manifest and device sector sizes differ")
    capacity = disk.session.geometry["sectors"] * sector_size
    # Validate all ranges before the first destructive operation.
    for operation, _ in operations:
        require(operation["offset"] + operation["size"] <= capacity,
                "factory operation exceeds device capacity")
    if persist is not None:
        begin, length = persist["offset"], persist["size"]
        require(begin > 0 and length > 0 and begin + length <= capacity, "persist exceeds device capacity")
        for operation, _ in operations:
            require(operation["offset"] >= begin + length or operation["offset"] + operation["size"] <= begin,
                    "factory operation overlaps persist")
        if initialize_persist(disk.read, sector_size, persist):
            print("Initializing the new identity region on the old disk layout", flush=True)
            disk.erase(begin, length)
    for operation, path in operations:
        offset, size = operation["offset"], operation["size"]
        print(f"{operation['operation']}: byte {offset}, {size} bytes", flush=True)
        if path is None:
            disk.erase(offset, size)
        else:
            disk.write_file_verified(offset, path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--loader", required=True)
    parser.add_argument("--memory", required=True, choices=("Ufs", "Nvme"))
    parser.add_argument("--without-openpilot", action="store_true")
    parser.add_argument("--no-reset", action="store_true")
    args = parser.parse_args()
    sector_size, operations = prepare_operations(args.manifest, args.output, not args.without_openpilot)
    persist = json.loads(args.manifest.read_text())["persist"]
    with EdlDisk(args.loader, args.memory) as disk:
        flash_operations(disk, sector_size, operations, persist)
        if not args.no_reset:
            disk.reset(1)


if __name__ == "__main__":
    main()
