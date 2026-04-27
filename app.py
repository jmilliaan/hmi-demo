"""
i-Prime AGV System — Demo Server
Three browser tabs:
  http://localhost:5000/        → Home Station HMI
  http://localhost:5000/agv/1  → AGV-1 onboard HMI
  http://localhost:5000/agv/2  → AGV-2 onboard HMI
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
# Zone / machine definitions
# ---------------------------------------------------------------------------
ZONE_A = [f"MRU-{i}" for i in range(1, 5)]
ZONE_B = [f"BTU-{i}" for i in range(9, 25)]
ZONE_C = [f"BTU-{i}" for i in range(1, 9)] + [f"STU-{i}" for i in range(1, 7)]
ZONE_D = [f"STU-{i}" for i in range(7, 11)]

MACHINE_TO_AGV = {m: 1 for m in ZONE_A + ZONE_B}
MACHINE_TO_AGV.update({m: 2 for m in ZONE_C + ZONE_D})

SINGLE_STOP = set(ZONE_A + ZONE_D)   # zones that accept exactly 1 stop
MULTI_STOP  = set(ZONE_B + ZONE_C)   # zones that accept 1 or 2 stops

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
    "agv": {1: _agv(87, "0001"), 2: _agv(62, "0050")},
    "calls": [],        # list of call dicts
    "next_action": {1: None, 2: None},   # pending load instruction per AGV
    "alarms": [],
    "log": [],          # event log for machine simulator tab
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
def add_call(machine: str, variant: int):
    agv_id = MACHINE_TO_AGV.get(machine)
    if not agv_id:
        return False
    call = dict(
        id=str(uuid.uuid4())[:8],
        machine=machine,
        variant=variant,
        agv=agv_id,
        status="QUEUED",
        created_at=time.time(),
    )
    with _lock:
        state["calls"].append(call)
        _add_log(f"Call from {machine} · V{variant} → AGV-{agv_id}")
    _broadcast()
    return True

# ---------------------------------------------------------------------------
# Dispatcher tick — runs in background thread every second
# ---------------------------------------------------------------------------
def _dispatcher_tick():
    with _lock:
        for agv_id in (1, 2):
            agv = state["agv"][agv_id]
            if agv["stage"] != "IDLE" or not agv["at_home"]:
                continue
            if state["next_action"][agv_id] is not None:
                continue

            pending = [c for c in state["calls"]
                       if c["agv"] == agv_id and c["status"] == "QUEUED"]
            if not pending:
                continue

            first = pending[0]
            stops = [first["machine"]]
            call_ids = [first["id"]]
            variant = first["variant"]

            # Zone B/C: batch a second call if available
            if first["machine"] in MULTI_STOP and len(pending) > 1:
                second = pending[1]
                stops.append(second["machine"])
                call_ids.append(second["id"])

            for c in state["calls"]:
                if c["id"] in call_ids:
                    c["status"] = "ASSIGNED"

            state["next_action"][agv_id] = {
                "stops": stops, "variant": variant, "call_ids": call_ids
            }
            _add_log(f"AGV-{agv_id} assigned → {' + '.join(stops)} · V{variant}")

def _dispatcher_loop():
    while True:
        time.sleep(1)
        try:
            _dispatcher_tick()
            _broadcast()
        except Exception as exc:
            print(f"[dispatcher] {exc}")

# ---------------------------------------------------------------------------
# AGV trip simulation — started in its own thread on Depart press
# ---------------------------------------------------------------------------
def _simulate_trip(agv_id: int, action: dict):
    stops    = action["stops"]
    call_ids = action["call_ids"]
    variant  = action["variant"]

    def _set(**kwargs):
        with _lock:
            state["agv"][agv_id].update(kwargs)

    def _set_call_status(cid, status):
        with _lock:
            for c in state["calls"]:
                if c["id"] == cid:
                    c["status"] = status

    def _set_all_call_status(status):
        with _lock:
            for c in state["calls"]:
                if c["id"] in call_ids:
                    c["status"] = status

    def _complete_calls():
        with _lock:
            state["calls"] = [c for c in state["calls"] if c["id"] not in call_ids]

    def _run():
        # Clear next_action, begin trip
        with _lock:
            state["next_action"][agv_id] = None
        _set(stage="EN_ROUTE", at_home=False,
             position=f"→ {stops[0]}", task={"stops": stops, "variant": variant})
        _set_all_call_status("IN-TRANSIT")
        _add_log(f"AGV-{agv_id} departed → {stops[0]}")
        _broadcast()

        time.sleep(8)

        # Arrive at stop 1
        _set(stage="AT_LINE", position=stops[0],
             last_rfid=f"RF_{stops[0].replace('-','_')}")
        _set_call_status(call_ids[0], "AT-LINE")
        _add_log(f"AGV-{agv_id} arrived at {stops[0]}")
        _broadcast()

        time.sleep(5)

        if len(stops) > 1:
            # Head to stop 2
            _set(stage="EN_ROUTE", position=f"→ {stops[1]}")
            _set_call_status(call_ids[0], "COMPLETED")
            _broadcast()
            time.sleep(6)

            # Arrive at stop 2
            _set(stage="AT_LINE", position=stops[1],
                 last_rfid=f"RF_{stops[1].replace('-','_')}")
            _set_call_status(call_ids[1], "AT-LINE")
            _add_log(f"AGV-{agv_id} arrived at {stops[1]}")
            _broadcast()
            time.sleep(5)

        # Return home
        _set(stage="RETURNING", position="→ HOME")
        _set_all_call_status("RETURNING")
        _broadcast()
        time.sleep(8)

        # Home
        with _lock:
            state["agv"][agv_id].update(
                stage="IDLE", position="HOME", at_home=True, task=None,
                last_rfid="HOME",
                battery=max(5, state["agv"][agv_id]["battery"] - random.randint(1, 4)),
                lateral_error=random.randint(-5, 5),
            )
        _complete_calls()
        _add_log(f"AGV-{agv_id} returned home")
        _broadcast()

    t = threading.Thread(target=_run, daemon=True)
    t.start()

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route("/")
def home():
    return render_template("home.html")

@app.route("/agv/<int:agv_id>")
def agv_page(agv_id):
    if agv_id not in (1, 2):
        return "Invalid AGV ID", 404
    return render_template("agv.html", agv_id=agv_id)

@app.route("/machine")
def machine_page():
    zones = {
        "A": ZONE_A, "B": ZONE_B, "C": ZONE_C, "D": ZONE_D
    }
    return render_template("machine.html", zones=zones)

@app.route("/api/call", methods=["POST"])
def api_call():
    data = request.get_json(force=True)
    machine = data.get("machine", "").strip()
    variant = int(data.get("variant", 0))
    if not machine or variant not in range(1, 7):
        return jsonify(ok=False, reason="bad input"), 400
    ok = add_call(machine, variant)
    return jsonify(ok=ok)

@app.route("/api/depart", methods=["POST"])
def api_depart():
    data = request.get_json(force=True)
    agv_id = int(data.get("agv_id", 0))
    if agv_id not in (1, 2):
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
    print("  i-Prime Demo Server")
    print("  Home HMI  →  http://localhost:5000/")
    print("  AGV-1 HMI →  http://localhost:5000/agv/1")
    print("  AGV-2 HMI →  http://localhost:5000/agv/2")
    print("  Machine   →  http://localhost:5000/machine")
    print("=" * 60)
    socketio.run(app, host="0.0.0.0", port=5000, debug=False)
