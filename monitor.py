"""
Tieline Audio Monitor
Polls Bridge-IT II + Merlin Plus via SNMP, sends SMS via Twilio on source changes.
Serves a web UI on port 8080 for settings and live status.
"""

import asyncio
import json
import logging
import sys
import threading
from datetime import datetime
from pathlib import Path
from functools import wraps

from flask import (
    Flask, render_template, request, redirect,
    url_for, session, flash, jsonify
)
from waitress import serve as waitress_serve

# ── App setup ──────────────────────────────────────────────────────────────────

app = Flask(__name__)
app.secret_key = "tl-mon-change-this-in-prod"

BASE_DIR      = Path(__file__).parent
SETTINGS_FILE = BASE_DIR / "settings.json"

DEFAULT_SETTINGS = {
    "transmitter_ip":     "192.168.3.20",
    "studio_ip":          "192.168.1.150",
    "snmp_community":     "public",
    "poll_interval":      30,
    "station_name":       "Moose FM",
    "twilio_account_sid": "",
    "twilio_auth_token":  "",
    "twilio_from_number": "",
    "alert_numbers":      [],
    "web_port":           8080,
    "web_password":       "admin",
}

SOURCE_LABELS = {
    0: "Normal (STL link)",
    1: "Backup (computer audio)",
    2: "SD card",
}

# Friendly names for the transmitter's audio inputs.
# Keys are the raw strings the Tieline reports in SNMP (left side of ':' in alarms).
# Edit the right-hand side to whatever wording you want to appear in SMS messages.
ALARM_SOURCE_NAMES = {
    "Input 1": "PlayoutONE (Input 1)",
    "Input 2": "Backup Computer (Input 2)",
}

# Friendly versions of the alarm descriptions the device sends.
# Keys are the raw strings from the Tieline; values are what goes into the SMS.
ALARM_DESC_REWRITES = {
    "Input silence detected": "audio has gone silent",
}

AUDIO_STATE_LABELS = {
    "normal":  "Normal — Live STL link",
    "backup":  "Backup — Computer audio",
    "sdcard":  "SD Card — File playback",
    "loss":    "Loss — No active source",
    "unknown": "Unknown",
}

AUDIO_STATE_COLOUR = {
    "normal":  "success",
    "backup":  "warning",
    "sdcard":  "warning",
    "loss":    "danger",
    "unknown": "secondary",
}

def _fmt_ts(dt: datetime) -> str:
    """Format a timestamp for SMS — cross-platform, no leading zeros on hour or day."""
    return f"{dt.hour % 12 or 12}:{dt.strftime('%M %p %a %b')} {dt.day}"


# ── SNMP OIDs ──────────────────────────────────────────────────────────────────

TX_OIDS = {
    "src0_state":    "1.3.6.1.4.1.37196.2.7.20.2.1.5.0",
    "src1_state":    "1.3.6.1.4.1.37196.2.7.20.2.1.5.1",
    "src2_state":    "1.3.6.1.4.1.37196.2.7.20.2.1.5.2",
    "cxn_state":     "1.3.6.1.4.1.37196.2.3.2.1.7.0",
}

ALARM_TABLE_OID = "1.3.6.1.4.1.37196.2.1.2.1"
ALARM_SOURCE_COL = "6"
ALARM_DESC_COL   = "7"

STUDIO_OIDS = {
    "cxn_state": "1.3.6.1.4.1.37196.2.3.2.1.7.0",
}

# ── Thread-safe state ──────────────────────────────────────────────────────────

_lock = threading.Lock()
_state = {
    "audio_state":      "unknown",
    "tx_reachable":     None,
    "studio_reachable": None,
    "active_alarms":    [],
    "last_change_time": None,
    "last_change_from": None,
    "last_change_to":   None,
    "last_sms_time":    None,
    "last_sms_to":      [],
    "last_poll_time":   None,
    "started":          datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
}


def get_state() -> dict:
    with _lock:
        return dict(_state)


def update_state(**kwargs) -> None:
    with _lock:
        _state.update(kwargs)

# ── Settings ───────────────────────────────────────────────────────────────────

def load_settings() -> dict:
    if SETTINGS_FILE.exists():
        try:
            data = json.loads(SETTINGS_FILE.read_text())
            return {**DEFAULT_SETTINGS, **data}
        except Exception:
            pass
    return dict(DEFAULT_SETTINGS)


def save_settings(settings: dict) -> None:
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2))

# ── SNMP (pure asyncio — called with await inside the monitor loop) ────────────

async def snmp_get(host: str, oids: dict, community: str) -> dict:
    """SNMP v2c GET — tries pysnmp first, falls back to subprocess snmpget."""
    results = await _snmp_get_pysnmp(host, oids, community)
    # If all values are None, pysnmp failed — try snmpget subprocess as fallback
    if all(v is None for v in results.values()):
        results = await asyncio.get_event_loop().run_in_executor(
            None, _snmp_get_subprocess, host, oids, community
        )
    return results


async def _snmp_get_pysnmp(host: str, oids: dict, community: str) -> dict:
    from pysnmp.hlapi.v3arch.asyncio import (
        get_cmd, SnmpEngine, CommunityData, UdpTransportTarget,
        ContextData, ObjectType, ObjectIdentity,
    )
    results = {}
    engine = SnmpEngine()
    try:
        transport = await UdpTransportTarget.create(
            (host, 161), timeout=5, retries=1
        )
        for key, oid in oids.items():
            try:
                errInd, errStat, _, varBinds = await get_cmd(
                    engine,
                    CommunityData(community, mpModel=1),
                    transport,
                    ContextData(),
                    ObjectType(ObjectIdentity(oid)),
                )
                results[key] = None if (errInd or errStat) else str(varBinds[0][1])
            except Exception:
                results[key] = None
    except Exception:
        results = {k: None for k in oids}
    finally:
        engine.close_dispatcher()
    return results


def _snmp_get_subprocess(host: str, oids: dict, community: str) -> dict:
    """Fallback: call snmpget CLI (available on Mac/Linux; bundled separately on Windows)."""
    import subprocess
    cmd = [
        "snmpget", "-v2c", "-c", community,
        "-t", "5", "-r", "1", host,
    ] + list(oids.values())
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        lines = result.stdout.strip().splitlines()
        values = {}
        for key, line in zip(oids.keys(), lines):
            val = line.split("=", 1)[-1].strip()
            if ":" in val:
                val = val.split(":", 1)[-1].strip().strip('"')
            values[key] = val if val else None
        return values
    except Exception:
        return {k: None for k in oids}


async def snmp_walk_alarms(host: str, community: str) -> list:
    """Walk the Tieline active-alarm table. Returns list of (source, description) tuples.
    Empty list means no active alarms or device unreachable."""
    result = await _snmp_walk_alarms_pysnmp(host, community)
    if result is None:
        log.info("Alarm walk: pysnmp failed, trying snmpwalk subprocess")
        result = await asyncio.get_event_loop().run_in_executor(
            None, _snmp_walk_alarms_subprocess, host, community
        )
    return result or []


async def _snmp_walk_alarms_pysnmp(host: str, community: str):
    """Walks the alarm table using GETNEXT (next_cmd) which works reliably across pysnmp versions.
    Returns list of (source, description) tuples, or None if pysnmp errored out entirely."""
    from pysnmp.hlapi.v3arch.asyncio import (
        next_cmd, SnmpEngine, CommunityData, UdpTransportTarget,
        ContextData, ObjectType, ObjectIdentity,
    )
    sources: dict = {}
    descs: dict = {}
    engine = SnmpEngine()
    try:
        transport = await UdpTransportTarget.create(
            (host, 161), timeout=5, retries=1
        )
        for col, bucket in ((ALARM_SOURCE_COL, sources), (ALARM_DESC_COL, descs)):
            base = f"{ALARM_TABLE_OID}.{col}"
            current_oid = base
            for _ in range(100):  # safety limit
                try:
                    errInd, errStat, _, varBinds = await next_cmd(
                        engine,
                        CommunityData(community, mpModel=1),
                        transport,
                        ContextData(),
                        ObjectType(ObjectIdentity(current_oid)),
                    )
                except Exception as exc:
                    log.warning(f"Alarm walk next_cmd failed at {current_oid}: {exc}")
                    break
                if errInd or errStat or not varBinds:
                    break
                name, val = varBinds[0]
                oid_str = str(name)
                if not oid_str.startswith(base + "."):
                    break
                idx = oid_str.rsplit(".", 1)[-1]
                bucket[idx] = str(val)
                current_oid = oid_str
    except Exception as exc:
        log.warning(f"Alarm walk (pysnmp) errored: {exc}")
        return None
    finally:
        engine.close_dispatcher()
    return [(sources.get(i, ""), descs.get(i, "")) for i in sorted(sources.keys())]


def _snmp_walk_alarms_subprocess(host: str, community: str) -> list:
    import subprocess
    sources = {}
    descs = {}
    for col, bucket in ((ALARM_SOURCE_COL, sources), (ALARM_DESC_COL, descs)):
        cmd = [
            "snmpwalk", "-v2c", "-c", community,
            "-t", "5", "-r", "1", host,
            f"{ALARM_TABLE_OID}.{col}",
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            for line in result.stdout.strip().splitlines():
                if "No Such" in line or "=" not in line:
                    continue
                left, right = line.split("=", 1)
                idx = left.strip().rsplit(".", 1)[-1]
                val = right.strip()
                if ":" in val:
                    val = val.split(":", 1)[-1].strip().strip('"')
                bucket[idx] = val
        except Exception:
            continue
    alarms = []
    for idx in sorted(sources.keys()):
        alarms.append((sources.get(idx, ""), descs.get(idx, "")))
    return alarms


def int_val(raw, default: int = -1) -> int:
    try:
        return int(str(raw))
    except (TypeError, ValueError):
        return default


def detect_audio_state(tx: dict) -> str:
    s0 = int_val(tx.get("src0_state"))
    s1 = int_val(tx.get("src1_state"))
    s2 = int_val(tx.get("src2_state"))
    if all(v == -1 for v in (s0, s1, s2)):
        return "loss"
    if s0 == 1:
        return "normal"
    if s1 == 1:
        return "backup"
    if s2 == 1:
        return "sdcard"
    return "loss"

# ── SMS ────────────────────────────────────────────────────────────────────────

log = logging.getLogger("tieline")


def send_sms(body: str, settings: dict) -> list:
    sid   = settings.get("twilio_account_sid", "").strip()
    token = settings.get("twilio_auth_token", "").strip()
    from_ = settings.get("twilio_from_number", "").strip()
    nums  = [n.strip() for n in settings.get("alert_numbers", []) if n.strip()]

    if not all([sid, token, from_, nums]):
        log.warning("SMS skipped — Twilio not fully configured")
        return []

    try:
        from twilio.rest import Client
        client = Client(sid, token)
        sent = []
        for number in nums:
            try:
                client.messages.create(to=number, from_=from_, body=body)
                log.info(f"SMS sent to {number}")
                sent.append(number)
            except Exception as exc:
                log.error(f"SMS to {number} failed: {exc}")
        return sent
    except Exception as exc:
        log.error(f"Twilio error: {exc}")
        return []

# ── Monitor loop (runs in its own thread with its own event loop) ──────────────

def _format_alarm(source: str, desc: str) -> str:
    """Build the human-readable SMS body for an alarm row.
    Looks up friendly names from ALARM_SOURCE_NAMES and ALARM_DESC_REWRITES;
    falls back to the raw device strings if no mapping exists."""
    raw_src = source.replace(":", "").strip() if source else ""
    raw_desc = (desc or "").strip()
    src  = ALARM_SOURCE_NAMES.get(raw_src, raw_src)
    body = ALARM_DESC_REWRITES.get(raw_desc, raw_desc)
    if src and body:
        return f"{src} {body}"
    return body or src or "Unknown alarm"


async def _monitor_loop() -> None:
    log.info("Monitor loop started")
    prev_audio    = None
    prev_tx_up    = None
    prev_st_up    = None
    prev_alarms = None  # set | None

    while True:
        try:
            log.info("Poll: starting")
            settings  = load_settings()
            community = settings["snmp_community"]
            name      = settings.get("station_name", "Station")

            log.info(f"Poll: snmp_get tx ({settings['transmitter_ip']})")
            tx     = await asyncio.wait_for(
                snmp_get(settings["transmitter_ip"], TX_OIDS, community), timeout=20
            )
            log.info(f"Poll: snmp_get studio ({settings['studio_ip']})")
            studio = await asyncio.wait_for(
                snmp_get(settings["studio_ip"], STUDIO_OIDS, community), timeout=20
            )

            tx_up = any(v is not None for v in tx.values())
            st_up = any(v is not None for v in studio.values())
            audio = detect_audio_state(tx)

            if tx_up:
                log.info("Poll: walking alarm table")
                alarm_list = await asyncio.wait_for(
                    snmp_walk_alarms(settings["transmitter_ip"], community), timeout=20
                )
                curr_alarms = {(s, d) for s, d in alarm_list}
                log.info(f"Poll: tx_up=True, audio={audio}, active_alarms={len(alarm_list)} {alarm_list}")
            else:
                alarm_list = []
                curr_alarms = prev_alarms if prev_alarms is not None else set()
                log.info(f"Poll: tx_up=False, retaining prev alarm set ({len(curr_alarms)} entries)")

            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            update_state(
                audio_state=audio,
                tx_reachable=tx_up,
                studio_reachable=st_up,
                active_alarms=[_format_alarm(s, d) for s, d in alarm_list],
                last_poll_time=now,
            )

            alerts = []

            if prev_audio is not None and audio != prev_audio:
                ts = _fmt_ts(datetime.now())
                if audio == "normal":
                    detail = f"RESOLVED - {name}\nStudio audio restored, back to normal\n{ts}"
                elif audio == "backup":
                    detail = f"ALERT - {name}\nStudio audio lost — switched to PlayoutONE at transmitter\n{ts}"
                elif audio == "sdcard":
                    detail = f"ALERT - {name}\nAll live sources lost — transmitter now playing from SD Card\n{ts}"
                else:
                    detail = f"ALERT - {name}\nNo audio source active — transmitter may be completely silent\n{ts}"
                alerts.append(detail)
                update_state(
                    last_change_time=now,
                    last_change_from=prev_audio,
                    last_change_to=audio,
                )
                log.warning(f"Audio source: {prev_audio} → {audio}")

            if prev_tx_up is not None and tx_up != prev_tx_up:
                ts = _fmt_ts(datetime.now())
                if tx_up:
                    alerts.append(f"RESOLVED - {name}\nTransmitter codec is back online\n{ts}")
                else:
                    alerts.append(f"ALERT - {name}\nTransmitter codec is not responding\nCheck device at {settings['transmitter_ip']}\n{ts}")
                log.warning(f"Transmitter reachable: {prev_tx_up} → {tx_up}")

            if prev_st_up is not None and st_up != prev_st_up:
                ts = _fmt_ts(datetime.now())
                if st_up:
                    alerts.append(f"RESOLVED - {name}\nStudio codec is back online\n{ts}")
                else:
                    alerts.append(f"ALERT - {name}\nStudio codec is not responding\nCheck device at {settings['studio_ip']}\n{ts}")
                log.warning(f"Studio reachable: {prev_st_up} → {st_up}")

            if prev_alarms is not None and tx_up:
                new_alarms     = curr_alarms - prev_alarms
                cleared_alarms = prev_alarms - curr_alarms
                for source, desc in sorted(new_alarms):
                    ts = _fmt_ts(datetime.now())
                    label = _format_alarm(source, desc)
                    alerts.append(f"ALERT - {name}\n{label}\n{ts}")
                    log.warning(f"Alarm raised: {label}")
                for source, desc in sorted(cleared_alarms):
                    ts = _fmt_ts(datetime.now())
                    label = _format_alarm(source, desc)
                    alerts.append(f"RESOLVED - {name}\n{label}\n{ts}")
                    log.warning(f"Alarm cleared: {label}")

            for body in alerts:
                sent = send_sms(body, settings)
                if sent:
                    update_state(last_sms_time=now, last_sms_to=sent)

            prev_audio  = audio
            prev_tx_up  = tx_up
            prev_st_up  = st_up
            prev_alarms = curr_alarms
            log.info("Poll: complete")
        except asyncio.TimeoutError:
            log.error("Poll: SNMP timeout — device unresponsive or walk hung")
        except Exception as exc:
            log.exception(f"Poll: unhandled error: {exc}")

        await asyncio.sleep(load_settings().get("poll_interval", 30))


def run_monitor() -> None:
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(_monitor_loop())

# ── Flask auth ─────────────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

# ── Routes ─────────────────────────────────────────────────────────────────────

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        settings = load_settings()
        if request.form.get("password") == settings.get("web_password", "admin"):
            session["logged_in"] = True
            return redirect(url_for("dashboard"))
        flash("Incorrect password.", "danger")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def dashboard():
    return render_template(
        "dashboard.html",
        state=get_state(),
        settings=load_settings(),
        audio_label=AUDIO_STATE_LABELS,
        audio_colour=AUDIO_STATE_COLOUR,
    )


@app.route("/api/status")
@login_required
def api_status():
    state = get_state()
    return jsonify({
        **state,
        "audio_label":      AUDIO_STATE_LABELS.get(state["audio_state"], "Unknown"),
        "audio_colour":     AUDIO_STATE_COLOUR.get(state["audio_state"], "secondary"),
        "tx_reachable":     state.get("tx_reachable"),
        "studio_reachable": state.get("studio_reachable"),
        "active_alarms":    state.get("active_alarms", []),
    })


@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings_page():
    if request.method == "POST":
        current = load_settings()
        raw_nums = request.form.get("alert_numbers", "")
        nums = [
            n.strip() for n in raw_nums.replace(",", "\n").splitlines()
            if n.strip()
        ]
        new_pw = request.form.get("web_password_new", "").strip()
        updated = {
            **current,
            "transmitter_ip":     request.form.get("transmitter_ip", "").strip(),
            "studio_ip":          request.form.get("studio_ip", "").strip(),
            "snmp_community":     request.form.get("snmp_community", "public").strip(),
            "poll_interval":      int(request.form.get("poll_interval", 30)),
            "station_name":       request.form.get("station_name", "").strip(),
            "twilio_account_sid": request.form.get("twilio_account_sid", "").strip(),
            "twilio_auth_token":  request.form.get("twilio_auth_token", "").strip(),
            "twilio_from_number": request.form.get("twilio_from_number", "").strip(),
            "alert_numbers":      nums,
            "web_port":           int(request.form.get("web_port", 8080)),
        }
        if new_pw:
            updated["web_password"] = new_pw
        save_settings(updated)
        flash("Settings saved.", "success")
        return redirect(url_for("settings_page"))

    return render_template("settings.html", settings=load_settings())


@app.route("/test-sms", methods=["POST"])
@login_required
def test_sms():
    settings = load_settings()
    name = settings.get("station_name", "Station")
    ts   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    body = f"{name} — Test SMS from Tieline Monitor\n{ts}"
    sent = send_sms(body, settings)
    if sent:
        flash(f"Test SMS sent to: {', '.join(sent)}", "success")
    else:
        flash("SMS not sent — check Twilio settings and alert numbers.", "danger")
    return redirect(url_for("settings_page"))

# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(BASE_DIR / "monitor.log"),
        ],
    )

    if not SETTINGS_FILE.exists():
        save_settings(DEFAULT_SETTINGS)
        log.info(f"Created default settings at {SETTINGS_FILE}")

    settings = load_settings()

    t = threading.Thread(target=run_monitor, daemon=True, name="monitor")
    t.start()
    log.info("Monitor thread started")

    port = settings.get("web_port", 8080)
    log.info(f"Web UI: http://0.0.0.0:{port}  (password: {settings.get('web_password','admin')})")
    waitress_serve(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
