"""
i-Prime AGV System — Demo Server (Goodyear)
Four browser tabs:
  http://localhost:5000/        → Home Station HMI
  http://localhost:5000/agv/1  → AGV-1 onboard HMI
  http://localhost:5000/agv/2  → AGV-2 onboard HMI
  http://localhost:5000/agv/3  → AGV-3 onboard HMI
  http://localhost:5000/machine → Machine calling simulator
"""

import threading
import time
import uuid
import random
from datetime import datetime
from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit

app = Flask(__name__)
app.config["SECRET_KEY"] = "iprime-demo-2026"
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# ---------------------------------------------------------------------------
# Line definitions (Goodyear)
# ---------------------------------------------------------------------------
LINES = ["VMI-1", "VMI-2", "VMI-3", "VMI-4", "VMI-5",
         "F2.5",
         "R2.5-1", "R2.5-2", "R2.5-3", "R2.5-4"]

LORRY_VARIANTS = ["SMALL_LORRY_12U", "BIG_LORRY_16U"]

# ---------------------------------------------------------------------------
# Shared system state
# ---------------------------------------------------------------------------
_lock = threading.Lock()

def _agv(battery, rfid):
    return dict(
        mode="AUTO", position="HOME", battery=battery,
        stage="IDLE", speed="HIGH",
        tape_detected=True, lateral_error=0,
        last_rfid=rfid, at_home=True, task=None,
    )

state = {
    "agv": {
        1: _agv(87, "0001"),
        2: _agv(62, "0050"),
        3: _agv(91, "0100"),
    },
    "calls": [],
    "next_action": {1: None, 2: None, 3: None},
    "alarms": [],
    "log": [],
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _ts():
    return datetime.now().strftime("%H:%M:%S")

def _add_log(msg):
    state["log"].insert(0, {"ts": _ts(), "msg": msg})
    state["log"] = state["log"][:40]

def _broadcast():
    socketio.emit("state", _serialize())

def _serialize():
    with _lock:
        import copy
        return copy.deepcopy({
            "agv": state["agv"],
            "calls": state["calls"],
            "next_action": state["next_action"],
            "alarms": state["alarms"],
            "log": state["log"],
            "ts": time.time(),
        })

# ---------------------------------------------------------------------------
# Call injection (from machine simulator)
# ---------------------------------------------------------------------------
def add_call(line: str, lorry_variant: str):
    call = dict(
        id=str(uuid.uuid4())[:8],
        line=line,
        lorry_variant=lorry_variant,
        agv=None,
        status="QUEUED",
        created_at=time.time(),
    )
    with _lock:
        state["calls"].append(call)
        _add_log(f"Call from {line} · {lorry_variant.split('_')[0]}")
    _broadcast()
    return True

# ---------------------------------------------------------------------------
# Dispatcher tick — first-idle-wins, ascending AGV-ID tiebreak
# ---------------------------------------------------------------------------
def _dispatcher_tick():
    with _lock:
        for c in state["calls"]:
            if c["status"] != "QUEUED":
                continue
            for agv_id in (1, 2, 3):
                agv = state["agv"][agv_id]
                if (agv["stage"] == "IDLE"
                        and agv["at_home"]
                        and state["next_action"][agv_id] is None):
                    c["status"] = "ASSIGNED"
                    c["agv"] = agv_id
                    state["next_action"][agv_id] = {
                        "line": c["line"],
                        "lorry_variant": c["lorry_variant"],
                        "call_id": c["id"],
                    }
                    _add_log(f"AGV-{agv_id} assigned → {c['line']} · {c['lorry_variant'].split('_')[0]}")
                    break

def _dispatcher_loop():
    while True:
        time.sleep(1)
        try:
            _dispatcher_tick()
            _broadcast()
        except Exception as exc:
            print(f"[dispatcher] {exc}")

# ---------------------------------------------------------------------------
# AGV trip simulation (single stop, shorter timings)
# ---------------------------------------------------------------------------
def _simulate_trip(agv_id: int, action: dict):
    line     = action["line"]
    call_id  = action["call_id"]
    variant  = action["lorry_variant"]

    def _set(**kwargs):
        with _lock:
            state["agv"][agv_id].update(kwargs)

    def _set_call(status):
        with _lock:
            for c in state["calls"]:
                if c["id"] == call_id:
                    c["status"] = status

    def _complete_call():
        with _lock:
            state["calls"] = [c for c in state["calls"] if c["id"] != call_id]

    def _run():
        with _lock:
            state["next_action"][agv_id] = None

        _set(stage="EN_ROUTE", at_home=False,
             position=f"→ {line}", task={"line": line, "lorry_variant": variant})
        _set_call("IN-TRANSIT")
        _add_log(f"AGV-{agv_id} departed → {line}")
        _broadcast()

        time.sleep(5)

        _set(stage="AT_LINE", position=line,
             last_rfid=f"RF_{line.replace('-','_').replace('.','_')}")
        _set_call("AT-LINE")
        _add_log(f"AGV-{agv_id} arrived at {line}")
        _broadcast()

        time.sleep(3)

        _set(stage="RETURNING", position="→ HOME")
        _set_call("RETURNING")
        _broadcast()

        time.sleep(5)

        with _lock:
            state["agv"][agv_id].update(
                stage="IDLE", position="HOME", at_home=True, task=None,
                last_rfid="HOME",
                battery=max(5, state["agv"][agv_id]["battery"] - random.randint(1, 3)),
                lateral_error=random.randint(-5, 5),
            )
        _complete_call()
        _add_log(f"AGV-{agv_id} returned home")
        _broadcast()

    threading.Thread(target=_run, daemon=True).start()

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route("/")
def home():
    return render_template("home.html")

@app.route("/agv/<int:agv_id>")
def agv_page(agv_id):
    if agv_id not in (1, 2, 3):
        return "Invalid AGV ID", 404
    return render_template("agv.html", agv_id=agv_id)

@app.route("/machine")
def machine_page():
    return render_template("machine.html", lines=LINES)

@app.route("/api/call", methods=["POST"])
def api_call():
    data = request.get_json(force=True)
    line    = data.get("line", "").strip()
    variant = data.get("lorry_variant", "").strip()
    if not line or line not in LINES or variant not in LORRY_VARIANTS:
        return jsonify(ok=False, reason="bad input"), 400
    add_call(line, variant)
    return jsonify(ok=True)

@app.route("/api/depart", methods=["POST"])
def api_depart():
    data   = request.get_json(force=True)
    agv_id = int(data.get("agv_id", 0))
    if agv_id not in (1, 2, 3):
        return jsonify(ok=False, reason="invalid agv"), 400
    with _lock:
        action = state["next_action"].get(agv_id)
        agv    = state["agv"][agv_id]
        if not action:
            return jsonify(ok=False, reason="no pending action")
        if not agv["at_home"] or agv["stage"] != "IDLE":
            return jsonify(ok=False, reason="AGV not ready")
        action_copy = dict(action)
    _simulate_trip(agv_id, action_copy)
    return jsonify(ok=True)

@app.route("/api/state")
def api_state():
    return jsonify(_serialize())

# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------
@socketio.on("connect")
def on_connect():
    emit("state", _serialize())

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    t = threading.Thread(target=_dispatcher_loop, daemon=True)
    t.start()
    print("=" * 60)
    print("  i-Prime Demo Server — Goodyear")
    print("  Home HMI  →  http://localhost:5000/")
    print("  AGV-1 HMI →  http://localhost:5000/agv/1")
    print("  AGV-2 HMI →  http://localhost:5000/agv/2")
    print("  AGV-3 HMI →  http://localhost:5000/agv/3")
    print("  Machine   →  http://localhost:5000/machine")
    print("=" * 60)
    socketio.run(app, host="0.0.0.0", port=5000, debug=False)
