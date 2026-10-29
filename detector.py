"""NODE 3: detector SitSmart (jalan di laptop).

- Subscribe telemetry dari 2 HP lewat MQTT cloud
- Proses data (shared/processing.py) -> status postur
- Publish status (retained) + alert, dan menerima perintah dari dashboard
- Menyajikan dashboard di http://localhost:8000
"""
from __future__ import annotations

import json
import os
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import paho.mqtt.client as mqtt

from shared.processing import Params, PostureEngine, Sample

ROOT = Path(__file__).resolve().parent


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


load_env(ROOT / ".env")
PROJECT_ID = os.getenv("PROJECT_ID", "sitsmart_demo")
MQTT_HOST = os.getenv("MQTT_HOST", "broker.hivemq.com")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_WS_URL = os.getenv("MQTT_WS_URL", "wss://broker.hivemq.com:8884/mqtt")
BASE = f"sitsmart/{PROJECT_ID}"
T_TELEMETRY = f"{BASE}/edge/+/telemetry"
T_STATUS = f"{BASE}/status"
T_ALERTS = f"{BASE}/alerts"
T_CONTROL = f"{BASE}/control"
ALERT_REPEAT_S = 10.0

params = Params(
    lean_threshold_deg=float(os.getenv("LEAN_THRESHOLD_DEG", "20")),
    lean_hold_s=float(os.getenv("LEAN_HOLD_S", "5")),
    sit_limit_s=float(os.getenv("SIT_LIMIT_S", "1800")),
)
engine = PostureEngine(params)
lock = threading.Lock()
state_info = {"last_state": None, "last_alert": 0.0}


def on_connect(client, userdata, flags, reason_code, properties=None):
    print(f"[mqtt] terhubung ke {MQTT_HOST}:{MQTT_PORT} ({reason_code})")
    client.subscribe([(T_TELEMETRY, 0), (T_CONTROL, 0)])
    print(f"[mqtt] subscribe {T_TELEMETRY} dan {T_CONTROL}")


def on_message(client, userdata, msg):
    try:
        data = json.loads(msg.payload.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return
    now = time.time()
    with lock:
        if msg.topic == T_CONTROL:
            handle_control(data, now)
            return
        parts = msg.topic.split("/")  # sitsmart/<pid>/edge/<n>/telemetry
        try:
            edge = int(parts[3])
            sample = Sample(
                t=now,
                rms=float(data["rms"]),
                gravity=[float(x) for x in data["gravity"]][:3],
                rot=float(data.get("rot", 0.0)),
            )
        except (IndexError, KeyError, ValueError, TypeError):
            return
        engine.feed(edge, sample)


def handle_control(data: dict, now: float) -> None:
    cmd = data.get("cmd")
    if cmd == "calibrate":
        print("[ctrl] kalibrasi:", "ok" if engine.calibrate() else "gagal (belum ada data HP Badan)")
    elif cmd == "monitor_on":
        engine.monitoring = True
    elif cmd == "monitor_off":
        engine.monitoring = False
    elif cmd == "reset_timer":
        engine.reset_timer(now)


def publish_loop(client: mqtt.Client) -> None:
    while True:
        time.sleep(1.0)
        now = time.time()
        with lock:
            status = engine.evaluate(now)
            body, seat = engine.body, engine.seat
            status["ts"] = int(now * 1000)
            status["body"] = None if body is None else {
                "rms": round(body.rms, 3), "rot": round(body.rot, 3), "age_s": round(now - body.t, 1)}
            status["seat"] = None if seat is None else {
                "rms": round(seat.rms, 3), "rot": round(seat.rot, 3), "age_s": round(now - seat.t, 1)}
        client.publish(T_STATUS, json.dumps(status), qos=0, retain=True)

        state = status["state"]
        if state in ("SLOUCH", "BREAK"):
            entering = state != state_info["last_state"]
            if entering or now - state_info["last_alert"] >= ALERT_REPEAT_S:
                alert = {"ts": status["ts"], "type": state, "risk": status["risk"], "reason": status["reason"]}
                client.publish(T_ALERTS, json.dumps(alert), qos=0)
                state_info["last_alert"] = now
                print(f"[ALERT] {state}: {status['reason']}")
        elif state != state_info["last_state"]:
            print(f"[state] {state}: {status['reason']}")
        state_info["last_state"] = state


class DashboardHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path.split("?")[0] == "/config.json":
            body = json.dumps({
                "project_id": PROJECT_ID,
                "ws_url": MQTT_WS_URL,
                "lean_threshold_deg": params.lean_threshold_deg,
                "sit_limit_s": params.sit_limit_s,
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def log_message(self, *args):
        pass


def main() -> None:
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"sitsmart-detector-{PROJECT_ID}")
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(MQTT_HOST, MQTT_PORT, keepalive=30)
    client.loop_start()
    threading.Thread(target=publish_loop, args=(client,), daemon=True).start()

    handler = partial(DashboardHandler, directory=str(ROOT / "dashboard"))
    server = ThreadingHTTPServer(("127.0.0.1", 8000), handler)
    print(f"[web] dashboard: http://localhost:8000  (PROJECT_ID={PROJECT_ID})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nberhenti.")
    finally:
        client.loop_stop()


if __name__ == "__main__":
    main()
