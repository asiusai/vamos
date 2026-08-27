#!/usr/bin/env python3
"""Dragon Q6A system health check.

Checks connectivity (NCM/wifi/bluetooth), openpilot processes, camera,
livestream, and live model health, and captures sample images.

Invoked from the host via:  dragon.py health
"""
import asyncio
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

CAMERA_SERVICES = ('narrowRoadCameraState', 'wideRoadCameraState', 'cabinCameraState')
LIVESTREAM_SERVICES = (
    'livestreamNarrowRoadEncodeData',
    'livestreamWideRoadEncodeData',
    'livestreamCabinEncodeData',
)
CAMERA_SERVICE_INFO = (
    ('narrowRoadCameraState', 'livestreamNarrowRoadEncodeData', 'road', 'VISION_STREAM_NARROW_ROAD'),
    ('wideRoadCameraState', 'livestreamWideRoadEncodeData', 'wide_road', 'VISION_STREAM_WIDE_ROAD'),
    ('cabinCameraState', 'livestreamCabinEncodeData', 'driver', 'VISION_STREAM_CABIN'),
)
EXPECTED_FRAME_INTERVAL_MS = 50.0
FRAME_INTERVAL_TOLERANCE_MS = 1.0
EXPECTED_STREAM_FPS = 20.0
STREAM_FPS_TOLERANCE = 2.0
EXPECTED_MODEL_FPS = 20.0
MODEL_FPS_TOLERANCE = 2.0
MODEL_AVERAGE_TIME_LIMIT_S = 0.033
MIN_REQUIRED_CAMERAS = int(os.environ.get("DRAGON_HEALTH_MIN_CAMERAS", "0"))
CAMERA_STREAM_WAIT_SECONDS = 30
MIN_IMAGE_DYNAMIC_RANGE = 8.0
MIN_LOW_LIGHT_DYNAMIC_RANGE = 3.0
MIN_LOW_LIGHT_SPATIAL_STD = 1.5
MAX_LOW_LIGHT_MEAN = 24.0
SNAPSHOT_DIR = "/tmp/dragon_health"
OPENPILOT_ROOT = os.environ.get("OPENPILOT_ROOT", "/data/openpilot")
DEFAULT_OPENPILOT_ROOT = "/data/openpilot"
PARAMS_DIR = Path(os.environ.get("PARAMS_DIR", "/data/params/d"))
OPENPILOT_PYTHON_PATHS = [
    OPENPILOT_ROOT,
    *(str(Path(OPENPILOT_ROOT) / submodule) for submodule in (
        "opendbc_repo",
        "msgq_repo",
        "rednose_repo",
        "teleoprtc_repo",
        "tinygrad_repo",
    )),
]
CUSTOM_MANAGER_LOG = "/tmp/dragon_health_manager.log"
custom_manager_process = None
custom_manager_log = None
livestream_original = None
livestream_changed = False

for python_path in reversed(OPENPILOT_PYTHON_PATHS):
    if Path(python_path).is_dir() and python_path not in sys.path:
        sys.path.insert(0, python_path)


def source_path(*parts):
    root = Path(OPENPILOT_ROOT)
    nested_root = root / "openpilot"
    source_root = nested_root if (nested_root / "system" / "manager").is_dir() else root
    return source_root.joinpath(*parts)


def import_messaging():
    try:
        from openpilot.cereal import messaging
    except ImportError:
        from cereal import messaging
    return messaging


def available_camera_info():
    try:
        from msgq.visionipc import VisionIpcClient
        from openpilot.cereal.visionipc import VisionStreamType
        available = set(VisionIpcClient.available_streams("camerad", block=False))
        return tuple(info for info in CAMERA_SERVICE_INFO
                     if getattr(VisionStreamType, info[3]).value in available)
    except Exception as e:
        warn(f"Cannot query available camera streams: {e}")
        return ()


def wait_for_camera_info(min_count, timeout=CAMERA_STREAM_WAIT_SECONDS):
    info = available_camera_info()
    if len(info) >= min_count:
        return info

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(0.1)
        info = available_camera_info()
        if len(info) >= min_count:
            return info
    return info


def section(title):
    print(f"\n{'=' * 60}\n  {title}\n{'=' * 60}")

def ok(msg):   print(f"  [OK]   {msg}")
def warn(msg): print(f"  [WARN] {msg}")
def fail(msg): print(f"  [FAIL] {msg}")

def run(cmd, timeout=10):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return -1, "", ""


def uses_system_openpilot_service():
    return os.path.realpath(OPENPILOT_ROOT) == os.path.realpath(DEFAULT_OPENPILOT_ROOT)


def write_param(name, value):
    path = PARAMS_DIR / name
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{name}.{os.getpid()}.tmp")
    temporary.write_bytes(value)
    os.replace(temporary, path)


def enable_health_cameras():
    global livestream_original, livestream_changed

    try:
        path = PARAMS_DIR / "IsLiveStreaming"
        if not livestream_changed:
            livestream_original = path.read_bytes() if path.exists() else None
            livestream_changed = True
        write_param("IsLiveStreaming", b"1")
        return True
    except Exception as e:
        fail(f"Could not enable cameras for health check: {e}")
        return False


def start_openpilot():
    global custom_manager_process, custom_manager_log

    if not enable_health_cameras():
        return False

    if uses_system_openpilot_service():
        run(["sudo", "sv", "up", "openpilot"], timeout=15)
        return True

    if custom_manager_process is not None and custom_manager_process.poll() is None:
        return True

    # A non-default checkout is used by the self-hosted CI runner. Stop the
    # installed checkout first so only one manager owns cameras and msgq.
    run(["sudo", "sv", "down", "openpilot"], timeout=30)

    launcher = Path(OPENPILOT_ROOT) / "launch_openpilot.sh"
    if not launcher.exists():
        fail(f"openpilot launcher not found at {launcher}")
        return False

    env = os.environ.copy()
    env["PYTHONPATH"] = OPENPILOT_ROOT
    env["OPENPILOT_ROOT"] = OPENPILOT_ROOT
    custom_manager_log = open(CUSTOM_MANAGER_LOG, "w")
    custom_manager_process = subprocess.Popen(
        [str(launcher)],
        cwd=OPENPILOT_ROOT,
        env=env,
        stdout=custom_manager_log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    print(f"  Started {OPENPILOT_ROOT} (log: {CUSTOM_MANAGER_LOG})")
    return True


def restore_system_openpilot():
    global custom_manager_process, custom_manager_log, livestream_changed

    if custom_manager_process is not None:
        if custom_manager_process.poll() is None:
            try:
                os.killpg(custom_manager_process.pid, signal.SIGTERM)
                custom_manager_process.wait(timeout=15)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(custom_manager_process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        custom_manager_process = None

    if custom_manager_log is not None:
        custom_manager_log.close()
        custom_manager_log = None

    if livestream_changed:
        try:
            if livestream_original is None:
                (PARAMS_DIR / "IsLiveStreaming").unlink(missing_ok=True)
            else:
                write_param("IsLiveStreaming", livestream_original)
        except Exception as e:
            warn(f"Could not restore IsLiveStreaming: {e}")
        livestream_changed = False

    if not uses_system_openpilot_service():
        run(["sudo", "sv", "up", "openpilot"], timeout=15)


def check_ncm():
    section("NCM (USB networking)")
    _, out, _ = run(["ip", "-4", "-o", "addr", "show"])
    for line in out.splitlines():
        if "usb" in line or "192.168.42." in line:
            ok(line.strip())
            return True
    warn("No NCM interface with 192.168.42.x found")
    return False


def check_wifi():
    section("WiFi")
    code, out, _ = run(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device"])
    if code != 0:
        code2, out2, _ = run(["ip", "link", "show", "wlan0"])
        if code2 == 0 and "UP" in out2:
            ok("wlan0 is UP (nmcli unavailable)")
            return True
        warn("Cannot query wifi")
        return False

    for line in out.splitlines():
        parts = line.split(":")
        if len(parts) >= 3 and parts[1] == "wifi" and parts[2] == "connected":
            conn = parts[3] if len(parts) > 3 else ""
            ok(f"WiFi connected: {conn}")
            return True

    warn("WiFi not connected")
    return False


def check_bluetooth():
    section("Bluetooth")
    code, out, _ = run(["bluetoothctl", "show"], timeout=5)
    if code == 0 and out:
        powered = "Powered: yes" in out
        (ok if powered else warn)("Bluetooth " + ("powered on" if powered else "not powered"))
        return powered

    code, out, _ = run(["hciconfig", "hci0"])
    if code == 0:
        up = "UP RUNNING" in out
        (ok if up else warn)("hci0 " + ("UP RUNNING" if up else "down"))
        return up

    try:
        present, powered = asyncio.run(bluez_adapter_state())
        if present:
            (ok if powered else warn)("Bluetooth " + ("powered on" if powered else "not powered"))
            return powered
    except Exception as e:
        warn(f"Cannot query BlueZ: {e}")
        return False

    warn("No Bluetooth controller found")
    return False


async def bluez_adapter_state():
    from dbus_fast.aio import MessageBus
    from dbus_fast.constants import BusType

    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    try:
        introspection = await bus.introspect("org.bluez", "/")
        proxy = bus.get_proxy_object("org.bluez", "/", introspection)
        managed_objects = await proxy.get_interface("org.freedesktop.DBus.ObjectManager").call_get_managed_objects()
        for interfaces in managed_objects.values():
            adapter = interfaces.get("org.bluez.Adapter1")
            if adapter is not None:
                return True, bool(adapter["Powered"].value)
        return False, False
    finally:
        bus.disconnect()


def check_processes():
    section("Openpilot Processes")
    results = {}
    try:
        messaging = import_messaging()
        sm = messaging.SubMaster(['managerState'])
        for _ in range(20):
            sm.update(200)
            if sm.updated['managerState']:
                break
        if sm.updated['managerState']:
            for p in sm['managerState'].processes:
                results[p.name] = {
                    'running': p.running,
                    'shouldBeRunning': p.shouldBeRunning,
                    'pid': p.pid,
                    'exitCode': p.exitCode,
                }
        else:
            warn("managerState not received — is manager running?")
    except Exception as e:
        warn(f"Cannot read managerState: {e}")

    if not results:
        warn("No process state available")
        return False

    all_ok = True
    for proc, info in sorted(results.items()):
        info = results[proc]
        if info['running']:
            ok(f"{proc:25s}  pid={info['pid']}")
        elif info['shouldBeRunning']:
            fail(f"{proc:25s}  SHOULD BE RUNNING (exit={info.get('exitCode', '?')})")
            all_ok = False
    return all_ok


def measure_fps(duration=5):
    section(f"Camera FPS ({duration}s sample)")
    try:
        messaging = import_messaging()
        import numpy as np
    except ImportError:
        fail("cereal.messaging not available")
        return {}

    camera_services = tuple(info[0] for info in available_camera_info())
    missing = set(CAMERA_SERVICES) - set(camera_services)
    for cam in CAMERA_SERVICES:
        if cam in missing:
            warn(f"{cam:25s}  camera unavailable, skipped")
    if not camera_services:
        warn("No camera streams available")
        return {}

    socks = {cam: messaging.sub_sock(cam, conflate=False, timeout=100) for cam in camera_services}
    time.sleep(0.2)
    for s in socks.values():
        messaging.drain_sock(s)

    frame_ids = {cam: [] for cam in camera_services}
    frame_times = {cam: [] for cam in camera_services}
    start = time.monotonic()
    while time.monotonic() - start < duration:
        for cam, sock in socks.items():
            for msg in messaging.drain_sock(sock):
                frame = getattr(msg, cam)
                frame_ids[cam].append(frame.frameId)
                frame_times[cam].append(frame.timestampSof)
        time.sleep(0.02)

    fps_results = {}
    for cam in camera_services:
        ids = frame_ids[cam]
        times = frame_times[cam]
        n = len(ids)
        skips = int(np.sum(np.diff(ids) > 1)) if n > 1 else 0

        if n == 0:
            fail(f"{cam:25s}  no frames received")
            fps_results[cam] = {'fps': 0, 'mean_ms': 0, 'ok': False}
        elif len(times) < 2:
            fail(f"{cam:25s}  only one timestamp received")
            fps_results[cam] = {'fps': 0, 'mean_ms': 0, 'ok': False}
        else:
            intervals_ms = np.diff(times) / 1e6
            mean_ms = float(np.mean(intervals_ms))
            fps = 1000.0 / mean_ms if mean_ms > 0 else 0
            min_ms = float(np.min(intervals_ms))
            max_ms = float(np.max(intervals_ms))
            cadence_ok = abs(mean_ms - EXPECTED_FRAME_INTERVAL_MS) <= FRAME_INTERVAL_TOLERANCE_MS
            fps_results[cam] = {'fps': fps, 'mean_ms': mean_ms, 'ok': cadence_ok}
            msg = (f"{cam:25s}  {fps:.2f} fps, {mean_ms:.2f} ms/frame "
                   f"({n} frames, {skips} skips, range {min_ms:.2f}-{max_ms:.2f} ms, "
                   f"expected {EXPECTED_FRAME_INTERVAL_MS:.0f}±{FRAME_INTERVAL_TOLERANCE_MS:.0f} ms)")
            (ok if cadence_ok else fail)(msg)
    return fps_results


def check_live_model(duration=5):
    section(f"Live Model ({duration}s sample)")
    code, _, _ = run(["pgrep", "-f", "openpilot.selfdrive.modeld.modeld"], timeout=2)
    if code != 0:
        ok("modeld inactive while the device is offroad")
        return True

    try:
        messaging = import_messaging()
    except ImportError:
        fail("cereal.messaging not available")
        return False

    sock = messaging.sub_sock("modelV2", conflate=False, timeout=100)
    time.sleep(0.2)
    messaging.drain_sock(sock)
    samples = []
    start = time.monotonic()
    while time.monotonic() - start < duration:
        for msg in messaging.drain_sock(sock):
            samples.append((
                msg.logMonoTime,
                msg.valid,
                msg.modelV2.frameId,
                msg.modelV2.modelExecutionTime,
                msg.modelV2.frameDropPerc,
            ))
        time.sleep(0.02)

    if len(samples) < 2:
        fail(f"modelV2 produced only {len(samples)} message(s)")
        return False

    elapsed = (samples[-1][0] - samples[0][0]) / 1e9
    fps = (len(samples) - 1) / elapsed if elapsed > 0 else 0
    execution_times = [sample[3] for sample in samples]
    average_time = sum(execution_times) / len(execution_times)
    max_drop = max(sample[4] for sample in samples)
    valid = sum(bool(sample[1]) for sample in samples)
    frame_ids_increase = all(b[2] > a[2] for a, b in zip(samples, samples[1:]))
    model_ok = (
        abs(fps - EXPECTED_MODEL_FPS) <= MODEL_FPS_TOLERANCE
        and average_time <= MODEL_AVERAGE_TIME_LIMIT_S
        and max_drop <= 10.0
        and valid == len(samples)
        and frame_ids_increase
    )
    msg = (
        f"modelV2 {fps:.2f} fps, {average_time * 1000:.2f} ms average, "
        f"{max(execution_times) * 1000:.2f} ms max, {max_drop:.1f}% max drop, "
        f"{valid}/{len(samples)} valid"
    )
    (ok if model_ok else fail)(msg)
    return model_ok


def measure_streaming(duration=5):
    section(f"Livestream Encoders ({duration}s sample)")
    try:
        messaging = import_messaging()
    except ImportError:
        fail("cereal.messaging not available")
        return {}

    livestream_services = tuple(info[1] for info in available_camera_info())
    missing = set(LIVESTREAM_SERVICES) - set(livestream_services)
    for service in LIVESTREAM_SERVICES:
        if service in missing:
            warn(f"{service:32s}  camera unavailable, skipped")
    if not livestream_services:
        warn("No camera streams available")
        return {}

    socks = {service: messaging.sub_sock(service, conflate=False, timeout=100)
             for service in livestream_services}
    time.sleep(0.2)
    for sock in socks.values():
        messaging.drain_sock(sock)

    frames = {service: {} for service in livestream_services}
    encoded_samples = {service: bytearray() for service in livestream_services}
    sample_started = {service: False for service in livestream_services}
    start = time.monotonic()
    while time.monotonic() - start < duration:
        for service, sock in socks.items():
            for msg in messaging.drain_sock(sock):
                encoded = getattr(msg, service)
                if len(encoded.data):
                    frames[service][encoded.idx.frameId] = (
                        msg.logMonoTime, encoded.width, encoded.height, len(encoded.data))
                    if encoded.idx.flags & 0x8:
                        sample_started[service] = True
                        encoded_samples[service].clear()
                    if sample_started[service]:
                        encoded_samples[service].extend(encoded.header)
                        encoded_samples[service].extend(encoded.data)
        time.sleep(0.01)

    results = {}
    for service, service_frames in frames.items():
        samples = sorted(service_frames.values())
        if len(samples) < 2:
            fail(f"{service:32s}  fewer than two encoded frames")
            results[service] = {'fps': 0, 'frames': len(samples), 'ok': False}
            continue

        elapsed = (samples[-1][0] - samples[0][0]) / 1e9
        fps = (len(samples) - 1) / elapsed if elapsed > 0 else 0
        dimensions = {(sample[1], sample[2]) for sample in samples}
        total_bytes = sum(sample[3] for sample in samples)
        visual_ok = validate_encoded_stream(service, encoded_samples[service])
        stream_ok = (
            abs(fps - EXPECTED_STREAM_FPS) <= STREAM_FPS_TOLERANCE
            and dimensions == {(1344, 760)}
            and total_bytes > 0
            and visual_ok
        )
        results[service] = {'fps': fps, 'frames': len(samples), 'ok': stream_ok}
        msg = (f"{service:32s}  {fps:.2f} fps, {len(samples)} frames, "
               f"{total_bytes / 1024:.0f} KiB, dimensions={sorted(dimensions)}")
        (ok if stream_ok else fail)(msg)
    return results


def validate_encoded_stream(service, encoded_data):
    try:
        import av
        from PIL import Image

        snapshot_dir = Path(SNAPSHOT_DIR)
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        name = service.removeprefix('livestream').removesuffix('EncodeData')
        encoded_path = snapshot_dir / f"{name}.h264"
        image_path = snapshot_dir / f"{name}_encoded.jpg"
        encoded_path.write_bytes(encoded_data)
        decoder = av.CodecContext.create("h264", "r")
        decoded_frames = decoder.decode(av.Packet(bytes(encoded_data)))
        if not decoded_frames:
            fail(f"{service:32s}  hardware stream produced no decodable frames")
            return False

        image = decoded_frames[0].to_ndarray(format="rgb24")
        Image.fromarray(image).save(image_path, "JPEG")
        valid, metrics = decoded_image_health(image)
        mode = "low-light" if metrics["dynamic_range"] < MIN_IMAGE_DYNAMIC_RANGE and metrics["low_light"] else "normal"
        message = (f"{service:32s}  decoded image range={metrics['dynamic_range']:.1f}, "
                   f"mean/std={metrics['mean']:.1f}/{metrics['spatial_std']:.1f}, "
                   f"row/column delta={metrics['row_delta']:.1f}/{metrics['column_delta']:.1f}, "
                   f"mode={mode}, saved to {image_path}")
        (ok if valid else fail)(message)
        return valid
    except Exception as e:
        fail(f"{service:32s}  hardware stream inspection failed: {e}")
        return False


def decoded_image_health(image):
    import numpy as np

    if len(image.shape) != 3 or image.shape[2] < 3:
        return False, {
            "dynamic_range": 0.0,
            "mean": 0.0,
            "spatial_std": 0.0,
            "row_delta": 0.0,
            "column_delta": 0.0,
            "low_light": False,
        }

    luminance = image.astype(np.float32).mean(axis=2)
    row_delta = float(np.abs(np.diff(luminance, axis=0)).mean())
    column_delta = float(np.abs(np.diff(luminance, axis=1)).mean())
    dynamic_range = float(np.percentile(luminance, 95) - np.percentile(luminance, 5))
    mean = float(luminance.mean())
    spatial_std = float(luminance.std())
    striped = row_delta > 15.0 and row_delta > max(4.0 * column_delta, 25.0)
    low_light = (
        mean <= MAX_LOW_LIGHT_MEAN
        and dynamic_range >= MIN_LOW_LIGHT_DYNAMIC_RANGE
        and spatial_std >= MIN_LOW_LIGHT_SPATIAL_STD
    )
    valid = (
        image.shape[:2] == (760, 1344)
        and (dynamic_range >= MIN_IMAGE_DYNAMIC_RANGE or low_light)
        and not striped
    )
    return valid, {
        "dynamic_range": dynamic_range,
        "mean": mean,
        "spatial_std": spatial_std,
        "row_delta": row_delta,
        "column_delta": column_delta,
        "low_light": low_light,
    }


def image_has_color(img):
    if len(img.shape) != 3 or img.shape[2] < 3:
        return False
    # JPEG snapshots should not be monochrome: require visible RGB channel spread.
    channel_delta = max(
        float(abs(img[:, :, 0].astype("int16") - img[:, :, 1].astype("int16")).mean()),
        float(abs(img[:, :, 0].astype("int16") - img[:, :, 2].astype("int16")).mean()),
        float(abs(img[:, :, 1].astype("int16") - img[:, :, 2].astype("int16")).mean()),
    )
    return channel_delta >= 2.0


def capture_snapshots_worker():
    try:
        from PIL import Image
        from msgq.visionipc import VisionIpcClient
        from openpilot.cereal.visionipc import VisionStreamType
        from openpilot.system.camerad.snapshot import extract_image
    except ImportError as e:
        fail(f"Cannot import snapshot deps: {e}")
        return False

    available = set(VisionIpcClient.available_streams("camerad", block=False))
    streams = {
        name: getattr(VisionStreamType, stream_name)
        for _, _, name, stream_name in CAMERA_SERVICE_INFO
        if getattr(VisionStreamType, stream_name).value in available
    }
    missing = {info[2] for info in CAMERA_SERVICE_INFO} - set(streams)
    for name in sorted(missing):
        warn(f"{name}: camera unavailable, skipped")
    if not streams:
        warn("No camera streams available")
        return True
    all_ok = True
    for name, stream_type in streams.items():
        try:
            client = VisionIpcClient("camerad", stream_type, True)
            deadline = time.monotonic() + 10
            while not client.connect(False) and time.monotonic() < deadline:
                time.sleep(0.1)
            if not client.is_connected():
                fail(f"{name}: could not connect to VisionIPC")
                all_ok = False
                continue
            buf = client.recv()
            if buf is None or len(buf.data) == 0:
                fail(f"{name}: empty frame")
                all_ok = False
                continue
            img = extract_image(buf)
            path = os.path.join(SNAPSHOT_DIR, f"{name}.jpg")
            Image.fromarray(img).save(path, "JPEG")
            if image_has_color(img):
                ok(f"{name}: {img.shape[1]}x{img.shape[0]} color saved to {path}")
            else:
                fail(f"{name}: {img.shape[1]}x{img.shape[0]} saved to {path}, but image is monochrome")
                all_ok = False
        except Exception as e:
            fail(f"{name}: {e}")
            all_ok = False
    return all_ok


def capture_snapshots():
    section("Camera Snapshots")
    snapshot_dir = Path(SNAPSHOT_DIR)
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    for name in ('road', 'wide_road', 'driver'):
        (snapshot_dir / f"{name}.jpg").unlink(missing_ok=True)

    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(OPENPILOT_PYTHON_PATHS)
    env["OPENPILOT_ROOT"] = OPENPILOT_ROOT
    try:
        proc = subprocess.run(
            [sys.executable, "-u", __file__, "--snapshot-worker"],
            cwd=OPENPILOT_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=45,
        )
    except subprocess.TimeoutExpired:
        fail("Snapshot worker timed out")
        return False

    if proc.stdout:
        print(proc.stdout.rstrip())
    if proc.stderr:
        print(proc.stderr.rstrip())
    if proc.returncode != 0:
        fail(f"Snapshot worker failed (exit={proc.returncode})")
        return False
    return True


def system_info():
    section("System Info")
    code, out, _ = run(["uname", "-r"])
    if code == 0:
        ok(f"Kernel: {out}")

    code, out, _ = run(["uptime", "-p"])
    if code == 0:
        ok(f"Uptime: {out}")

    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith(("MemTotal:", "MemAvailable:")):
                    ok(line.strip())
    except Exception:
        pass

    try:
        temps = []
        for tz in sorted(Path("/sys/class/thermal").glob("thermal_zone*")):
            try:
                t = int((tz / "temp").read_text().strip()) / 1000
                name = (tz / "type").read_text().strip()
                temps.append(f"{name}={t:.0f}C")
            except Exception:
                continue
        if temps:
            ok(f"Temps: {', '.join(temps[:6])}")
    except Exception:
        pass

    code, out, _ = run(["df", "-h", "/data"])
    if code == 0 and len(out.splitlines()) > 1:
        ok(f"Disk: {out.splitlines()[-1].strip()}")


def wait_for_openpilot(timeout=300):
    section("Waiting for openpilot")
    if not start_openpilot():
        return False
    try:
        messaging = import_messaging()
    except ImportError:
        warn("cereal not importable, proceeding anyway")
        return True

    sock = messaging.sub_sock('managerState', timeout=1000)
    start = time.monotonic()
    last_print = 0
    while time.monotonic() - start < timeout:
        if messaging.drain_sock(sock):
            elapsed = time.monotonic() - start
            if not enable_health_cameras():
                return False
            camera_start = time.monotonic()
            while time.monotonic() - camera_start < 60:
                camera_process = active_camerad_process()
                if camera_process is not None:
                    ok(f"openpilot and {camera_process} are up ({elapsed:.0f}s)")
                    return True
                time.sleep(0.5)
            fail("camerad did not start within 60s")
            return False
        now = time.monotonic()
        if now - last_print >= 10:
            elapsed = now - start
            compiling = run(["pgrep", "-f", "scons"])[0] == 0
            print(f"  ... {'compiling' if compiling else 'waiting for manager'} ({elapsed:.0f}s)")
            last_print = now
    fail(f"openpilot did not start within {timeout}s")
    return False


def active_camerad_process():
    for process in ("camerad_v1", "camerad"):
        if run(["pgrep", "-x", process], timeout=2)[0] == 0:
            return process
    return None


def run_checks():
    system_info()
    ready = wait_for_openpilot()
    if not ready:
        print("\n  Proceeding with checks anyway...\n")

    camera_count = len(wait_for_camera_info(MIN_REQUIRED_CAMERAS))
    cameras_present = camera_count >= MIN_REQUIRED_CAMERAS
    if cameras_present:
        ok(f"Detected {camera_count} camera stream(s); required {MIN_REQUIRED_CAMERAS}")
    else:
        fail(f"Detected {camera_count} camera stream(s); required {MIN_REQUIRED_CAMERAS}")

    results = {
        'camera_presence': cameras_present,
        'ncm':          check_ncm(),
        'wifi':         check_wifi(),
        'bluetooth':    check_bluetooth(),
        'processes':    check_processes(),
        'model':        check_live_model(),
        'fps':          measure_fps(),
        'streaming':    measure_streaming(),
        'snapshots':    capture_snapshots(),
    }

    section("Summary")
    fps = results.get('fps', {})
    fps_ok = all(v['ok'] for v in fps.values())
    streaming = results.get('streaming', {})
    streaming_ok = all(v['ok'] for v in streaming.values())
    checks = [
        ("Camera presence", results['camera_presence']),
        ("NCM",          results['ncm']),
        ("WiFi",         results['wifi']),
        ("Bluetooth",    results['bluetooth']),
        ("Processes",    results['processes']),
        ("Live Model",   results['model']),
        ("Snapshots",    results['snapshots']),
        ("Camera FPS",   fps_ok),
        ("Streaming",    streaming_ok),
    ]

    all_pass = True
    for name, passed in checks:
        (ok if passed else fail)(name)
        all_pass = all_pass and passed

    if fps:
        fps_str = ", ".join(f"{c.replace('CameraState','')}={v['fps']:.2f}fps/{v['mean_ms']:.2f}ms" for c, v in fps.items())
        print(f"\n  FPS: {fps_str}")
    print(f"  Snapshots: {SNAPSHOT_DIR}")
    print()
    print("  ALL CHECKS PASSED" if all_pass else "  SOME CHECKS FAILED")
    return 0 if all_pass else 1


def main():
    if sys.argv[1:] == ["--snapshot-worker"]:
        return 0 if capture_snapshots_worker() else 1

    print(f"Dragon Q6A Health Check — {time.strftime('%Y-%m-%d %H:%M:%S')}")
    try:
        return run_checks()
    finally:
        restore_system_openpilot()


if __name__ == "__main__":
    sys.exit(main())
