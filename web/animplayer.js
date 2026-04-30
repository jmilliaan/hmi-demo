// animplayer.js — AGV animation player logic

const canvas = document.getElementById('canvas');
const ctx    = canvas.getContext('2d');

const BAR_TOP    = 44;   // load bar height
const BAR_BOTTOM = 48;   // toolbar height

const AGV_SIZE   = 30;
const TROLLEY_W  = 28;
const TROLLEY_H  = 18;

const ACTION_COLORS = {
  pickup:   '#50DC78',
  release:  '#F4A261',
  exchange: '#50C8FF',
};

// ── Application state ─────────────────────────────────────────────────────

const state = {
  nodes:    {},   // {ID: {x,y,type,heading}}
  sequence: [],   // [{node, action}]
  view:     { offsetX: 0, offsetY: 0, zoom: 1 },
  bgImage:  null,
  imgW:     0,
  imgH:     0,

  // Player controls
  playing:        false,
  timeScale:      1,
  agvSpeed:       120,
  actionDuration: 1.5,
  showGrid:       true,
  showLabels:     true,

  // Pan
  isPanning:    false,
  panStart:     { sx: 0, sy: 0 },
  panViewStart: { offsetX: 0, offsetY: 0 },
  mouse:        { sx: 0, sy: 0 },

  // Animation time
  lastTimestamp: null,
  elapsed:       0,   // total elapsed simulation time (for pulse effects)
};

// ── Sequence walker ───────────────────────────────────────────────────────

const walker = {
  currentStep:  0,
  agvPos:       { x: 0, y: 0 },
  agvHeading:   0,
  trolleyState: 'empty',   // 'empty' | 'carrying'
  trolleyPos:   null,      // {x,y} when detached; null when carrying or not yet placed
  phase:        'idle',    // 'idle' | 'action_pause' | 'moving' | 'done'
  actionTimer:  0,
};

function actionDurationFor(action) {
  if (action === 'move')     return 0;
  if (action === 'exchange') return state.actionDuration * 2;
  return state.actionDuration;
}

function applyActionEffect(action, nodePos) {
  switch (action) {
    case 'pickup':
      walker.trolleyState = 'carrying';
      walker.trolleyPos   = null;
      break;
    case 'release':
      walker.trolleyState = 'empty';
      walker.trolleyPos   = { x: nodePos.x, y: nodePos.y };
      break;
    case 'exchange':
      // Leave old trolley, immediately pick up new one
      walker.trolleyPos   = { x: nodePos.x, y: nodePos.y };
      walker.trolleyState = 'carrying';
      // The left trolley stays briefly then we clear it after pickup
      // For simplicity: leave trolleyPos set (shows dropped trolley), carrying = true
      // The "old" trolley visual stays at this position permanently
      break;
    case 'move':
    default:
      break;
  }
}

function resetWalker() {
  if (state.sequence.length === 0) return;
  const startNodeId = state.sequence[0].node;
  const startPt     = state.nodes[startNodeId];
  if (!startPt) return;

  walker.currentStep  = 0;
  walker.agvPos       = { x: startPt.x, y: startPt.y };
  walker.agvHeading   = startPt.heading;
  walker.trolleyState = 'empty';
  walker.trolleyPos   = null;
  walker.phase        = 'action_pause';   // perform action at first node immediately
  walker.actionTimer  = 0;
  state.elapsed       = 0;
}

function updateWalker(dt) {
  if (walker.phase === 'idle' || walker.phase === 'done') return;

  if (walker.phase === 'action_pause') {
    const action   = state.sequence[walker.currentStep].action;
    const duration = actionDurationFor(action);
    walker.actionTimer += dt;

    if (walker.actionTimer >= duration) {
      // Apply effect and advance
      const nodeId  = state.sequence[walker.currentStep].node;
      const nodePt  = state.nodes[nodeId];
      if (nodePt) applyActionEffect(action, nodePt);

      walker.currentStep++;
      if (walker.currentStep >= state.sequence.length) {
        walker.phase = 'done';
        return;
      }
      walker.phase       = 'moving';
      walker.actionTimer = 0;
    }
    return;
  }

  if (walker.phase === 'moving') {
    const targetNodeId = state.sequence[walker.currentStep].node;
    const targetPt     = state.nodes[targetNodeId];
    if (!targetPt) { walker.phase = 'done'; return; }

    const path   = [{ x: walker.agvPos.x, y: walker.agvPos.y }, targetPt];
    const result = advanceAlongPath(walker.agvPos, path, 1, state.agvSpeed, dt);

    // Compute heading from direction of travel
    const dx = targetPt.x - walker.agvPos.x;
    const dy = targetPt.y - walker.agvPos.y;
    if (Math.hypot(dx, dy) > 0.5) {
      const targetHeading = ((Math.atan2(dy, dx) * 180 / Math.PI) + 360) % 360;
      // Smooth heading toward target at 300 deg/s
      let diff = targetHeading - walker.agvHeading;
      if (diff >  180) diff -= 360;
      if (diff < -180) diff += 360;
      const maxTurn = 300 * dt;
      walker.agvHeading += Math.max(-maxTurn, Math.min(maxTurn, diff));
      walker.agvHeading = ((walker.agvHeading % 360) + 360) % 360;
    }

    walker.agvPos = result.pos;

    // Check arrival
    if (result.targetIdx >= path.length) {
      walker.agvPos = { x: targetPt.x, y: targetPt.y };
      walker.phase  = 'action_pause';
      walker.actionTimer = 0;
    }
  }
}

// ── Canvas resize ─────────────────────────────────────────────────────────

function resizeCanvas() {
  canvas.width  = window.innerWidth;
  canvas.height = window.innerHeight - BAR_TOP - BAR_BOTTOM;
  canvas.style.marginTop = `${BAR_TOP}px`;
}
window.addEventListener('resize', resizeCanvas);
resizeCanvas();

// ── File loading ──────────────────────────────────────────────────────────

function fitImageToCanvas() {
  const z = Math.min(canvas.width / state.imgW, canvas.height / state.imgH) * 0.9;
  state.view.zoom    = z;
  state.view.offsetX = canvas.width  / 2 - state.imgW * z / 2;
  state.view.offsetY = canvas.height / 2 - state.imgH * z / 2;
}

document.getElementById('loadJson').addEventListener('change', (e) => {
  const file = e.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = (ev) => {
    try {
      const data = JSON.parse(ev.target.result);
      state.nodes    = data.NODES    || {};
      state.sequence = normaliseSequence(data.SEQUENCE || []);
      document.getElementById('loadStatus').textContent =
        `${file.name} — ${Object.keys(state.nodes).length} nodes, ${state.sequence.length} seq entries`;
      state.playing = false;
      updatePlayButton();
      resetWalker();
      updateStatusBadge();
      updateStepCounter();
    } catch { alert('Invalid JSON file.'); }
  };
  reader.readAsText(file);
});

document.getElementById('loadImg').addEventListener('change', (e) => {
  const file = e.target.files[0];
  if (!file) return;
  const url = URL.createObjectURL(file);
  const img = new Image();
  img.onload = () => {
    state.bgImage = img;
    state.imgW    = img.naturalWidth;
    state.imgH    = img.naturalHeight;
    fitImageToCanvas();
    URL.revokeObjectURL(url);
  };
  img.src = url;
});

// ── Player controls ───────────────────────────────────────────────────────

const btnPlayPause = document.getElementById('btnPlayPause');
const btnRestart   = document.getElementById('btnRestart');
const statusBadge  = document.getElementById('statusBadge');
const stepCounter  = document.getElementById('stepCounter');

function updatePlayButton() {
  btnPlayPause.textContent = state.playing ? '⏸ Pause' : '▶ Play';
  btnPlayPause.className   = state.playing ? 'active' : '';
}

function updateStatusBadge() {
  const phaseLabels = {
    idle:         'IDLE',
    moving:       'MOVING',
    done:         'DONE',
    action_pause: currentActionLabel(),
  };
  const label    = phaseLabels[walker.phase] || 'IDLE';
  const cssClass = {
    IDLE:     'status-idle',
    MOVING:   'status-moving',
    PICKUP:   'status-pickup',
    RELEASE:  'status-release',
    EXCHANGE: 'status-exchange',
    DONE:     'status-done',
  }[label] || 'status-idle';
  statusBadge.textContent = label;
  statusBadge.className   = `${cssClass}`;
}

function currentActionLabel() {
  if (walker.currentStep >= state.sequence.length) return 'DONE';
  const action = state.sequence[walker.currentStep]?.action || 'move';
  return action === 'move' ? 'MOVING' : action.toUpperCase();
}

function updateStepCounter() {
  stepCounter.textContent = `step ${walker.currentStep} / ${state.sequence.length}`;
}

btnPlayPause.addEventListener('click', () => {
  if (state.sequence.length === 0) return;
  if (walker.phase === 'done') resetWalker();
  state.playing = !state.playing;
  updatePlayButton();
});

btnRestart.addEventListener('click', () => {
  resetWalker();
  state.playing = false;
  updatePlayButton();
  updateStatusBadge();
  updateStepCounter();
});

document.getElementById('speedSelect').addEventListener('change', (e) => {
  state.timeScale = parseFloat(e.target.value);
});

document.getElementById('agvSpeedInput').addEventListener('change', (e) => {
  state.agvSpeed = Math.max(1, parseFloat(e.target.value) || 120);
});

document.getElementById('actionDurInput').addEventListener('change', (e) => {
  state.actionDuration = Math.max(0, parseFloat(e.target.value) || 1.5);
});

document.getElementById('showGrid').addEventListener('change', (e) => {
  state.showGrid = e.target.checked;
});

document.getElementById('showLabels').addEventListener('change', (e) => {
  state.showLabels = e.target.checked;
});

// Space bar play/pause
window.addEventListener('keydown', (e) => {
  if (e.code === 'Space' && e.target.tagName !== 'INPUT') {
    e.preventDefault();
    btnPlayPause.click();
  }
});

// ── Pan + zoom ────────────────────────────────────────────────────────────

canvas.addEventListener('mousedown', (e) => {
  if (e.button === 1) {
    e.preventDefault();
    state.isPanning    = true;
    state.panStart     = { sx: e.clientX, sy: e.clientY };
    state.panViewStart = { offsetX: state.view.offsetX, offsetY: state.view.offsetY };
  }
});

canvas.addEventListener('mouseup', (e) => {
  if (e.button === 1) state.isPanning = false;
});

canvas.addEventListener('mousemove', (e) => {
  state.mouse.sx = e.clientX;
  state.mouse.sy = e.clientY - BAR_TOP;
  if (state.isPanning) {
    state.view.offsetX = state.panViewStart.offsetX + (e.clientX - state.panStart.sx);
    state.view.offsetY = state.panViewStart.offsetY + (e.clientY - state.panStart.sy);
  }
});

canvas.addEventListener('wheel', (e) => {
  e.preventDefault();
  const factor  = e.deltaY < 0 ? 1 + ZOOM_STEP : 1 - ZOOM_STEP;
  const newZoom = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, state.view.zoom * factor));
  const { ix, iy } = screenToImg(state.mouse.sx, state.mouse.sy, state.view);
  state.view.zoom    = newZoom;
  state.view.offsetX = state.mouse.sx - ix * newZoom;
  state.view.offsetY = state.mouse.sy - iy * newZoom;
}, { passive: false });

// ── Drawing ───────────────────────────────────────────────────────────────

function drawRoundRect(x, y, w, h, r, fill, stroke) {
  ctx.beginPath();
  ctx.roundRect(x, y, w, h, r);
  if (fill)   { ctx.fillStyle = fill;   ctx.fill(); }
  if (stroke) { ctx.strokeStyle = stroke; ctx.lineWidth = 2; ctx.stroke(); }
}

function drawActionIndicator(sx, sy, action, progress) {
  const col = ACTION_COLORS[action];
  if (!col) return;

  // Progress arc (grows from 0 to full circle as action completes)
  const startAngle = -Math.PI / 2;
  const endAngle   = startAngle + 2 * Math.PI * Math.min(progress, 1);
  ctx.beginPath();
  ctx.arc(sx, sy, AGV_SIZE / 2 + 10, startAngle, endAngle);
  ctx.strokeStyle = col;
  ctx.lineWidth   = 3;
  ctx.lineCap     = 'round';
  ctx.stroke();

  // Text label above AGV
  ctx.fillStyle    = col;
  ctx.font         = 'bold 11px monospace';
  ctx.textAlign    = 'center';
  ctx.textBaseline = 'bottom';
  ctx.fillText(action.toUpperCase(), sx, sy - AGV_SIZE / 2 - 14);
}

function drawTrolley(sx, sy, attached) {
  const w = attached ? 22 : TROLLEY_W;
  const h = attached ? 14 : TROLLEY_H;
  drawRoundRect(sx - w / 2, sy - h / 2, w, h, 2, '#F4A261', '#c07830');
}

function drawAGV(sx, sy, heading) {
  const half = AGV_SIZE / 2;
  drawRoundRect(sx - half, sy - half, AGV_SIZE, AGV_SIZE, 4, '#E63946', '#ffffff');
  drawHeadingArrow(ctx, sx, sy, heading, 16, '#ffffff', 2);
}

function drawActivePulse(sx, sy, t) {
  const r = DOT_RADIUS + 6 + 3 * Math.sin(t * 5);
  ctx.beginPath();
  ctx.arc(sx, sy, r, 0, Math.PI * 2);
  ctx.strokeStyle = 'rgba(255,255,255,0.6)';
  ctx.lineWidth   = 1.5;
  ctx.stroke();
}

function drawScene(timestamp) {
  const cw = canvas.width;
  const ch = canvas.height;

  ctx.fillStyle = '#f0f0f0';
  ctx.fillRect(0, 0, cw, ch);

  // Background
  if (state.bgImage) {
    const { sx, sy } = imgToScreen(0, 0, state.view);
    ctx.drawImage(state.bgImage, sx, sy, state.imgW * state.view.zoom, state.imgH * state.view.zoom);
  }

  if (state.showGrid && state.imgW) {
    drawGrid(ctx, state.imgW, state.imgH, state.view);
  }

  // Sequence path lines
  const validSeq = state.sequence.filter(e => state.nodes[e.node]);
  if (validSeq.length > 1) {
    ctx.beginPath();
    ctx.strokeStyle = 'rgba(255,100,200,0.5)';
    ctx.lineWidth   = 2;
    ctx.setLineDash([6, 4]);
    validSeq.forEach(({ node }, i) => {
      const pt      = state.nodes[node];
      const { sx, sy } = imgToScreen(pt.x, pt.y, state.view);
      if (i === 0) ctx.moveTo(sx, sy); else ctx.lineTo(sx, sy);
    });
    ctx.stroke();
    ctx.setLineDash([]);
  }

  // Node dots
  for (const [id, pt] of Object.entries(state.nodes)) {
    const { sx, sy } = imgToScreen(pt.x, pt.y, state.view);
    const col = dotColorForType(pt.type);

    // Active target pulse
    const targetId = walker.phase === 'moving'
      ? state.sequence[walker.currentStep]?.node
      : null;
    if (id === targetId) {
      drawActivePulse(sx, sy, state.elapsed);
    }

    ctx.beginPath();
    ctx.arc(sx, sy, DOT_RADIUS, 0, Math.PI * 2);
    ctx.fillStyle   = col;
    ctx.fill();
    ctx.strokeStyle = '#ffffff';
    ctx.lineWidth   = 1;
    ctx.stroke();

    drawHeadingArrow(ctx, sx, sy, pt.heading, 18, col, 1.5);

    if (state.showLabels) {
      ctx.fillStyle    = '#1a1a2a';
      ctx.font         = '10px monospace';
      ctx.textAlign    = 'left';
      ctx.textBaseline = 'middle';
      ctx.fillText(id, sx + DOT_RADIUS + 3, sy);
    }
  }

  // Sequence step labels
  if (state.showLabels) {
    validSeq.forEach(({ node, action }, i) => {
      const pt = state.nodes[node];
      const { sx, sy } = imgToScreen(pt.x, pt.y, state.view);
      ctx.fillStyle    = '#aa2288';
      ctx.font         = '10px monospace';
      ctx.textAlign    = 'left';
      ctx.textBaseline = 'bottom';
      ctx.fillText(`${i}:${action[0]}`, sx + DOT_RADIUS + 1, sy - 2);
    });
  }

  // Detached trolley
  if (walker.trolleyState === 'empty' && walker.trolleyPos) {
    const { sx, sy } = imgToScreen(walker.trolleyPos.x, walker.trolleyPos.y, state.view);
    drawTrolley(sx, sy, false);
  }

  // AGV
  if (walker.phase !== 'idle' && state.sequence.length > 0) {
    const { sx: ax, sy: ay } = imgToScreen(walker.agvPos.x, walker.agvPos.y, state.view);

    // Attached trolley (behind AGV visually)
    if (walker.trolleyState === 'carrying') {
      drawTrolley(ax, ay, true);
    }

    drawAGV(ax, ay, walker.agvHeading);

    // Action indicator during pause
    if (walker.phase === 'action_pause') {
      const action   = state.sequence[walker.currentStep]?.action;
      const duration = actionDurationFor(action);
      if (action && action !== 'move' && duration > 0) {
        const progress = walker.actionTimer / duration;
        drawActionIndicator(ax, ay, action, progress);
      }
    }
  }

  // HUD overlay
  drawHUD(cw, ch);

  drawHeadingLegend(ctx, cw);
}

function drawHUD(cw, ch) {
  const lines = [
    `Step ${walker.currentStep} / ${state.sequence.length}   Phase: ${walker.phase}   Speed: ${state.timeScale}×`,
  ];
  ctx.save();
  ctx.fillStyle    = 'rgba(240,240,248,0.92)';
  ctx.fillRect(0, ch - 22, cw, 22);
  ctx.fillStyle    = '#1a1a2a';
  ctx.font         = '11px monospace';
  ctx.textBaseline = 'middle';
  ctx.textAlign    = 'left';
  ctx.fillText(lines[0], 10, ch - 11);
  ctx.restore();
}

// ── Main loop ─────────────────────────────────────────────────────────────

function tick(timestamp) {
  if (state.lastTimestamp === null) state.lastTimestamp = timestamp;
  const rawDt = (timestamp - state.lastTimestamp) / 1000;
  state.lastTimestamp = timestamp;

  const dt = Math.min(rawDt, 0.1) * state.timeScale;   // cap at 100ms to avoid spiral on tab switch

  if (state.playing && walker.phase !== 'idle' && walker.phase !== 'done') {
    state.elapsed += dt;
    updateWalker(dt);
    updateStatusBadge();
    updateStepCounter();
    if (walker.phase === 'done') {
      state.playing = false;
      updatePlayButton();
    }
  }

  drawScene(timestamp);
  requestAnimationFrame(tick);
}

requestAnimationFrame(tick);
