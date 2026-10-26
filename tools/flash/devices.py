"""Read Dragon USB devices without claiming an interface or changing its mode."""

from dataclasses import dataclass
from pathlib import Path

USB_ROOT = Path('/sys/bus/usb/devices')
EDL_IDS = {(0x05c6, 0x9008), (0x3801, 0x9008)}


@dataclass(frozen=True)
class Device:
  port: str
  serial: str
  mode: str
  speed: str
  product: str
  vendor: int
  product_id: int
  bus: int = 0
  address: int = 0


def devices(root: Path = USB_ROOT) -> list[Device]:
  result = []
  for path in sorted(root.glob('*')):
    try:
      vendor = int((path / 'idVendor').read_text().strip(), 16)
      product_id = int((path / 'idProduct').read_text().strip(), 16)
      product = (path / 'product').read_text().strip() if (path / 'product').exists() else 'Qualcomm EDL'
      if (vendor, product_id) in EDL_IDS:
        mode = 'EDL'
      elif (vendor, product_id) == (0x1d6b, 0x0103) and product == 'Dragon Q6A NCM':
        mode = 'NCM'
      else:
        continue
      serial = (path / 'serial').read_text().strip() if (path / 'serial').exists() else '-'
      speed = float((path / 'speed').read_text())
      label = 'USB 3' if speed >= 5000 else 'USB 2' if speed == 480 else f'{speed:g} Mb/s'
      bus = int((path / 'busnum').read_text()) if (path / 'busnum').exists() else 0
      address = int((path / 'devnum').read_text()) if (path / 'devnum').exists() else 0
      result.append(Device(path.name, serial, mode, label, product, vendor, product_id, bus, address))
    except (OSError, ValueError):
      continue  # A device can disconnect while sysfs is being read.
  return result


def select_edl() -> Device:
  available = [device for device in devices() if device.mode == 'EDL']
  if len(available) != 1:
    raise ValueError(f'Expected one EDL device, found {len(available)}. Connect only the Dragon to flash.')
  return available[0]


def show_devices() -> None:
  available = devices()
  if not available:
    print('No Dragon devices connected over USB.')
    return
  rows = [('PORT', 'SERIAL', 'MODE', 'LINK', 'DEVICE')]
  rows += [(device.port, device.serial, device.mode, device.speed, device.product) for device in available]
  widths = [max(len(row[index]) for row in rows) for index in range(5)]
  for row in rows:
    print('  '.join(value.ljust(width) for value, width in zip(row, widths)).rstrip())
