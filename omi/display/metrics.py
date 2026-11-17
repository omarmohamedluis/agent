"""Low-frequency local metrics, sampled exclusively by the display thread."""
from pathlib import Path


class LocalMetrics:
    def __init__(self, proc: Path = Path("/proc/stat"), thermal: Path = Path("/sys/class/thermal/thermal_zone0/temp")):
        self.proc, self.thermal = proc, thermal
        self.previous = None

    def sample(self):
        result = {}
        try:
            fields = [int(value) for value in self.proc.read_text().splitlines()[0].split()[1:9]]
            total, idle = sum(fields), fields[3] + fields[4]
            if self.previous:
                elapsed = total - self.previous[0]
                if elapsed > 0:
                    result["cpu"] = max(0.0, min(100.0, 100 * (1 - (idle - self.previous[1]) / elapsed)))
            self.previous = total, idle
        except (OSError, ValueError, IndexError):
            pass
        try:
            result["temperature"] = int(self.thermal.read_text().strip()) / 1000
        except (OSError, ValueError):
            pass
        return result
