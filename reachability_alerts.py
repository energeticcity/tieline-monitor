"""Debounce codec management checks without delaying actual audio alarms.

This file contains no network or SMS operations. State records notification
attempts, not delivery receipts. Persist before returning an event so a monitor
restart cannot reset the anti-flood window. The existing sender reports failures.
"""
import json
import math
import os
from pathlib import Path


class ReachabilityAlerts:
    FAILURE_POLLS = 3
    FAILURE_SECONDS = 60
    RECOVERY_POLLS = 2
    RECOVERY_SECONDS = 30
    COOLDOWN_SECONDS = 15 * 60

    def __init__(self, path):
        self.path = Path(path)
        self.devices = {}
        self.candidates = {}
        self.load_error = None
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if data.get("version") != 1 or not isinstance(data.get("devices"), dict):
                    raise ValueError("Invalid notification state")
                for key, row in data["devices"].items():
                    if not isinstance(key, str) or len(key) > 100 or not isinstance(row, dict):
                        raise ValueError("Invalid device state")
                    if row.get("stable") is not None and type(row["stable"]) is not bool:
                        raise ValueError("Invalid stable state")
                    if type(row.get("down_notice")) is not bool:
                        raise ValueError("Invalid notice state")
                    stamp = row.get("last_failure_notice")
                    if stamp is not None and (type(stamp) not in (int, float) or not math.isfinite(stamp) or stamp < 0):
                        raise ValueError("Invalid notification time")
                self.devices = data["devices"]
            except (OSError, ValueError, TypeError, AttributeError):
                # Do not replace unknown evidence or turn corruption into a new
                # burst of texts. Audio alarms do not depend on this file.
                self.load_error = "Reachability notification state cannot be read"

    def _save(self):
        temporary = self.path.with_name(self.path.name + ".tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump({"version": 1, "devices": self.devices}, stream, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.path)

    def observe(self, key, reachable, now, notifications_enabled=True, maximum_sample_gap=120):
        """Return offline/recovered once per confirmed, notification-eligible event.

        Unknown checks break confirmation; observations separated by more than
        two minutes cannot certify continuity. Restarted processes reconfirm
        samples, retaining the last outage notice and cooldown on disk.
        """
        if self.load_error:
            raise RuntimeError(self.load_error)
        if not isinstance(key, str) or not key or len(key) > 100:
            raise ValueError("A bounded device identity is required")
        if type(now) not in (int, float) or not math.isfinite(now) or now < 0:
            raise ValueError("A valid observation time is required")
        if reachable is None:
            self.candidates.pop(key, None)
            return None
        if type(reachable) is not bool:
            raise ValueError("Reachability must be boolean or unknown")
        if type(maximum_sample_gap) not in (int, float) or not math.isfinite(maximum_sample_gap) or maximum_sample_gap <= 0:
            raise ValueError("A positive sample gap is required")
        previous = self.devices.get(key)
        row = dict(previous or {"stable": None, "last_failure_notice": None, "down_notice": False})
        candidate = self.candidates.get(key)
        if (not candidate or candidate["value"] != reachable or now < candidate["last"]
                or now - candidate["last"] > maximum_sample_gap):
            candidate = {"value": reachable, "samples": 0, "since": now, "last": now}
        candidate["samples"] += 1
        candidate["last"] = now
        self.candidates[key] = candidate

        if row["stable"] is None and reachable:
            row["stable"] = True  # quiet healthy startup, never a fake recovery
        elif row["stable"] != reachable:
            polls = self.RECOVERY_POLLS if reachable else self.FAILURE_POLLS
            seconds = self.RECOVERY_SECONDS if reachable else self.FAILURE_SECONDS
            if candidate["samples"] >= polls and now - candidate["since"] >= seconds:
                row["stable"] = reachable

        event = None
        if row["stable"] is False and not reachable and not row["down_notice"]:
            last = row["last_failure_notice"]
            if notifications_enabled and (last is None or now - last >= self.COOLDOWN_SECONDS):
                row["down_notice"] = True
                row["last_failure_notice"] = now
                event = "offline"
        elif row["stable"] is True and reachable and row["down_notice"]:
            row["down_notice"] = False
            if notifications_enabled:
                event = "recovered"

        if row != previous:
            self.devices[key] = row
            try:
                self._save()
            except OSError:
                # A failed reservation must not permit uncontrolled sends on
                # subsequent polls. Critical audio alarms still run separately.
                self.load_error = "Reachability notification state cannot be saved"
                raise
        return event
