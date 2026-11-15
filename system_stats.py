"""Read host telemetry on demand while the live banner is enabled."""
import subprocess
import time
from pathlib import Path
from threading import Lock


def memory(used, free):
    return {"used": used, "free": free, "percent": round(100 * used / (used + free))} if used + free else None


def host_memory():
    try:
        values = {key: int(value.split()[0]) * 1024
                  for key, value in (line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())}
        free = values["MemAvailable"]
        return memory(values["MemTotal"] - free, free)
    except (OSError, ValueError, KeyError):
        return None


def cpu_ticks():
    try:
        line = Path("/proc/stat").read_text().splitlines()[0]
        fields = list(map(int, line.split()[1:9]))
        return sum(fields), fields[3] + fields[4]
    except (OSError, ValueError, IndexError):
        return None


def cpu_temperature():
    packages, cores = [], []
    for hwmon in Path("/sys/class/hwmon").glob("hwmon*"):
        try:
            if (hwmon / "name").read_text().strip() not in {"coretemp", "k10temp", "zenpower", "cpu_thermal", "k8temp"}:
                continue
        except OSError:
            continue
        for entry in hwmon.glob("temp*_input"):
            try:
                value = round(int(entry.read_text()) / 1000, 1)
                label = entry.with_name(entry.name.replace("_input", "_label"))
                name = label.read_text().strip() if label.exists() else ""
            except (OSError, ValueError):
                continue
            if 0 < value < 150:
                (packages if name.startswith(("Package", "Tdie", "Tctl")) else cores).append(value)
    return max(packages or cores, default=None)


def gpu_stats():
    try:
        result = subprocess.run(
            ["nvidia-smi", "-i", "0", "--query-gpu=temperature.gpu,utilization.gpu,memory.used,memory.free",
             "--format=csv,noheader,nounits"], capture_output=True, text=True, check=True, timeout=2,
        )
        temp, utilization, used, free = result.stdout.strip().split(",")
        vram = memory(int(used) * 1048576, int(free) * 1048576)
        return {"temperature": int(temp), "utilization": int(utilization)}, vram
    except (OSError, ValueError, subprocess.SubprocessError):
        return None, None


class Stats:
    def __init__(self):
        self.lock = Lock()
        self.previous_ticks = None
        self.last_sample = 0
        self.cached = {}

    def sample(self):
        with self.lock:
            now = time.monotonic()
            if now - self.last_sample < 2 and self.cached:
                return self.cached
            ticks = cpu_ticks()
            utilization = None
            if ticks and self.previous_ticks:
                total = ticks[0] - self.previous_ticks[0]
                idle = ticks[1] - self.previous_ticks[1]
                if total > 0:
                    utilization = min(100, max(0, round(100 * (total - idle) / total)))
            self.previous_ticks = ticks
            gpu, vram = gpu_stats()
            self.cached = {"cpu": {"temperature": cpu_temperature(), "utilization": utilization},
                           "ram": host_memory(), "gpu": gpu, "vram": vram}
            self.last_sample = now
            return self.cached
