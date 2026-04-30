// cpicker.js — Coordinate picker logic

const canvas   = document.getElementById('canvas');
const ctx      = canvas.getContext('2d');

// ── Application state ─────────────────────────────────────────────────────

const state = {
  nodes:    {},       // { ID: {x, y, type, heading} }
  sequence: [],       // [ {node: ID, action: 'move'|'pickup'|'release'|'exchange'} ]
  view:     { offsetX: 0, offsetY: 0, zoom: 1 },
  mode:     'NODE',   // 'NODE' | 'SEQUENCE'
  bgImage:  null,
  imgW:     0,
  imgH:     0,
  outputFilename: 'coords.json',

  // Pending placement (node being pinned)
  pending:  null,     // {x, y} image coords

  // Live mouse state
  mouseScreen: { sx: 0, sy: 0 },
  mouseImg:    { ix: 0, iy: 0 },

  // Pan state
  isPanning:       false,
  panStart:        { sx: 0, sy: 0 },
  panViewStart:    { offsetX: 0, offsetY: 0 },

  // Sequence-mode hover
  hoveredNode: null,

  // Action picker
  actionPickerNode: null,   // node ID awaiting action selection
};

// ── DOM references ────────────────────────────────────────────────────────

const startupModal   = document.getElementById('startupModal');
const nodeFormModal  = document.getElementById('nodeFormModal');
const actionPicker   = document.getElementById('actionPicker');
const seqPanel       = document.getElementById('seqPanel');
const seqPanelTitle  = document.getElementById('seqPanelTitle');
const seqList        = document.getElementById('seqList');
const modeBadge      = document.getElementById('modeBadge');
const hudCoords      = document.getElementById('hudCoords');
const hudZoom        = document.getElementById('hudZoom');
const hudCounts      = document.getElementById('hudCounts');
const hudFile        = document.getElementById('hudFile');
const filenameInput  = document.getElementById('filenameInput');
const loadJsonInput  = document.getElementById('loadJsonInput');
const loadImgInput   = document.getElementById('loadImgInput');
const nodeIdInput    = document.getElementById('nodeId');
const nodeHeadingInput = document.getElementById('nodeHeading');
const btnWaypoint       = document.getElementById('btnWaypoint');
const btnSeqPoint       = document.getElementById('btnSeqPoint');
const confirmClearModal = document.getElementById('confirmClearModal');

let selectedType = 'waypoint';

// ── Clear / restart ───────────────────────────────────────────────────────

document.getElementById('clearBtn').addEventListener('click', () => {
  confirmClearModal.showModal();
});

document.getElementById('confirmClearCancel').addEventListener('click', () => {
  confirmClearModal.close();
});

document.getElementById('confirmClearOk').addEventListener('click', () => {
  confirmClearModal.close();

  // Reset all state
  state.nodes    = {};
  state.sequence = [];
  state.bgImage  = null;
  state.imgW     = 0;
  state.imgH     = 0;
  state.pending  = null;
  state.mode     = 'NODE';
  state.hoveredNode      = null;
  state.actionPickerNode = null;

  // Reset form inputs so the user can pick new files
  loadJsonInput.value    = '';
  loadImgInput.value     = '';
  filenameInput.value    = 'coords.json';
  state.outputFilename   = 'coords.json';

  // Reset UI
  hideActionPicker();
  updateSeqPanel();
  updateModeBadge();
  hudFile.textContent = '→ coords.json';

  // Reopen startup modal
  startupModal.showModal();
});

// ── Canvas resize ─────────────────────────────────────────────────────────

function resizeCanvas() {
  canvas.width  = window.innerWidth;
  canvas.height = window.innerHeight;
}
window.addEventListener('resize', resizeCanvas);
resizeCanvas();

// ── Image loading ─────────────────────────────────────────────────────────

function loadImage(file) {
  const url = URL.createObjectURL(file);
  const img = new Image();
  img.onload = () => {
    state.bgImage = img;
    state.imgW    = img.naturalWidth;
    state.imgH    = img.naturalHeight;
    const z = Math.min(canvas.width / state.imgW, (canvas.height - 26) / state.imgH) * 0.95;
    state.view.zoom    = z;
    state.view.offsetX = canvas.width  / 2 - state.imgW * z / 2;
    state.view.offsetY = (canvas.height - 26) / 2 - state.imgH * z / 2;
    URL.revokeObjectURL(url);
  };
  img.src = url;
}

// ── Startup modal ─────────────────────────────────────────────────────────

startupModal.showModal();

loadJsonInput.addEventListener('change', (e) => {
  const file = e.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = (ev) => {
    try {
      const data = JSON.parse(ev.target.result);
      if (data.NODES)    state.nodes    = data.NODES;
      if (data.SEQUENCE) state.sequence = normaliseSequence(data.SEQUENCE);
      updateSeqPanel();
    } catch { alert('Invalid JSON.'); }
  };
  reader.readAsText(file);
});

loadImgInput.addEventListener('change', (e) => {
  if (e.target.files[0]) loadImage(e.target.files[0]);
});

document.getElementById('startBtn').addEventListener('click', () => {
  const name = filenameInput.value.trim();
  if (name) {
    state.outputFilename = name;
    hudFile.textContent  = `→ ${name}`;
  }
  startupModal.close();
  updateSeqPanel();
  requestAnimationFrame(drawLoop);
});

// ── Node type toggle ──────────────────────────────────────────────────────

function setType(type) {
  selectedType = type;
  btnWaypoint.className = type === 'waypoint'  ? 'active-waypoint' : '';
  btnSeqPoint.className = type === 'seq_point' ? 'active-seq'      : '';
}

btnWaypoint.addEventListener('click', () => setType('waypoint'));
btnSeqPoint.addEventListener('click', () => setType('seq_point'));

// ── Node form ─────────────────────────────────────────────────────────────

function openNodeForm(ix, iy) {
  state.pending = { x: Math.round(ix), y: Math.round(iy) };
  nodeIdInput.value = '';
  setType('waypoint');
  nodeHeadingInput.value = '0';
  nodeFormModal.showModal();
  requestAnimationFrame(() => nodeIdInput.focus());
}

function saveNode() {
  const id = nodeIdInput.value.trim();
  if (!id) { nodeIdInput.focus(); return; }
  const hdg = ((parseFloat(nodeHeadingInput.value) % 360) + 360) % 360;
  state.nodes[id] = {
    x:       state.pending.x,
    y:       state.pending.y,
    type:    selectedType,
    heading: isNaN(hdg) ? 0 : hdg,
  };
  state.pending = null;
  nodeFormModal.close();
  updateSeqPanel();
}

document.getElementById('nodeConfirmBtn').addEventListener('click', saveNode);
document.getElementById('nodeCancelBtn').addEventListener('click', () => {
  state.pending = null;
  nodeFormModal.close();
});

nodeFormModal.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') {
    // Don't trigger save when focus is on type toggle buttons
    if (e.target === btnWaypoint || e.target === btnSeqPoint) return;
    e.preventDefault();
    saveNode();
  }
});

// Close without saving if dialog is dismissed via Escape
nodeFormModal.addEventListener('cancel', () => { state.pending = null; });

// ── Action picker ─────────────────────────────────────────────────────────

actionPicker.querySelectorAll('button[data-action]').forEach(btn => {
  btn.addEventListener('click', () => {
    const action = btn.dataset.action;
    const nodeId = state.actionPickerNode;
    if (nodeId) {
      state.sequence.push({ node: nodeId, action });
      updateSeqPanel();
    }
    hideActionPicker();
  });
});

function showActionPicker(nodeId, clientX, clientY) {
  state.actionPickerNode = nodeId;
  const picker = actionPicker;
  picker.style.display = 'flex';
  // Prevent going off right/bottom edge
  const w = 200, h = 160;
  picker.style.left = `${Math.min(clientX + 10, window.innerWidth  - w - 10)}px`;
  picker.style.top  = `${Math.min(clientY,       window.innerHeight - h - 10)}px`;
}

function hideActionPicker() {
  state.actionPickerNode = null;
  actionPicker.style.display = 'none';
}

// Dismiss action picker on outside click
document.addEventListener('mousedown', (e) => {
  if (actionPicker.style.display === 'flex' && !actionPicker.contains(e.target)) {
    hideActionPicker();
  }
});

// ── Keyboard ──────────────────────────────────────────────────────────────

window.addEventListener('keydown', (e) => {
  // Don't intercept keys while a modal is open
  if (startupModal.open || nodeFormModal.open) return;

  if (e.key === 'e' || e.key === 'E') {
    hideActionPicker();
    state.mode = state.mode === 'NODE' ? 'SEQUENCE' : 'NODE';
    updateModeBadge();
  }

  if ((e.key === 's' || e.key === 'S') && !e.ctrlKey && !e.metaKey) {
    saveJSON();
  }
});

// ── Mouse: canvas ─────────────────────────────────────────────────────────

canvas.addEventListener('contextmenu', (e) => {
  e.preventDefault();
  if (actionPicker.style.display === 'flex') { hideActionPicker(); return; }

  if (state.mode === 'NODE') {
    const keys = Object.keys(state.nodes);
    if (keys.length) {
      delete state.nodes[keys[keys.length - 1]];
      updateSeqPanel();
    }
  } else {
    if (state.sequence.length) {
      state.sequence.pop();
      updateSeqPanel();
    }
  }
});

canvas.addEventListener('mousedown', (e) => {
  if (e.button === 0) {
    // Dismiss action picker on canvas click (already handled by document mousedown, but explicit here)
    if (actionPicker.style.display === 'flex') { hideActionPicker(); return; }

    if (state.mode === 'NODE') {
      if (!nodeFormModal.open && !startupModal.open) {
        openNodeForm(state.mouseImg.ix, state.mouseImg.iy);
      }
    } else {
      const hit = findNodeAt(
        state.mouseScreen.sx, state.mouseScreen.sy,
        state.nodes, state.view
      );
      if (hit) {
        const pt = state.nodes[hit];
        if (pt.type === 'waypoint') {
          state.sequence.push({ node: hit, action: 'move' });
          updateSeqPanel();
        } else {
          showActionPicker(hit, e.clientX, e.clientY);
        }
      }
    }
  }

  if (e.button === 1) {
    e.preventDefault();
    state.isPanning    = true;
    state.panStart     = { sx: e.clientX, sy: e.clientY };
    state.panViewStart = { offsetX: state.view.offsetX, offsetY: state.view.offsetY };
    canvas.style.cursor = 'grabbing';
  }
});

canvas.addEventListener('mouseup', (e) => {
  if (e.button === 1) {
    state.isPanning = false;
    canvas.style.cursor = state.mode === 'NODE' ? 'crosshair' : 'default';
  }
});

canvas.addEventListener('mousemove', (e) => {
  state.mouseScreen.sx = e.clientX;
  state.mouseScreen.sy = e.clientY;

  const img = screenToImg(e.clientX, e.clientY, state.view);
  state.mouseImg.ix = Math.max(0, Math.min(state.imgW || 9999, img.ix));
  state.mouseImg.iy = Math.max(0, Math.min(state.imgH || 9999, img.iy));

  if (state.isPanning) {
    const dx = e.clientX - state.panStart.sx;
    const dy = e.clientY - state.panStart.sy;
    state.view.offsetX = state.panViewStart.offsetX + dx;
    state.view.offsetY = state.panViewStart.offsetY + dy;
  }

  if (state.mode === 'SEQUENCE') {
    state.hoveredNode = findNodeAt(e.clientX, e.clientY, state.nodes, state.view);
    canvas.style.cursor = state.hoveredNode ? 'pointer' : 'default';
  } else {
    canvas.style.cursor = state.isPanning ? 'grabbing' : 'crosshair';
  }

  hudCoords.textContent = `(${Math.round(state.mouseImg.ix)}, ${Math.round(state.mouseImg.iy)})`;
  hudZoom.textContent   = `zoom: ${state.view.zoom.toFixed(2)}×`;
  hudCounts.textContent = `nodes: ${Object.keys(state.nodes).length} | seq: ${state.sequence.length}`;
});

canvas.addEventListener('wheel', (e) => {
  e.preventDefault();
  const factor  = e.deltaY < 0 ? 1 + ZOOM_STEP : 1 - ZOOM_STEP;
  const newZoom = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, state.view.zoom * factor));
  const mx = state.mouseScreen.sx;
  const my = state.mouseScreen.sy;
  const { ix, iy } = screenToImg(mx, my, state.view);
  state.view.zoom    = newZoom;
  state.view.offsetX = mx - ix * newZoom;
  state.view.offsetY = my - iy * newZoom;
}, { passive: false });

// ── Sequence panel (DOM) ──────────────────────────────────────────────────

function updateSeqPanel() {
  seqPanel.style.display = state.sequence.length > 0 ? 'block' : 'none';
  seqPanelTitle.textContent = `SEQUENCE (${state.sequence.length})`;
  seqList.innerHTML = '';
  state.sequence.forEach(({ node, action }, i) => {
    const row = document.createElement('div');
    row.className = 'seq-entry';
    row.innerHTML =
      `<span class="seq-idx">${i}</span>` +
      `<span class="seq-node">${node}</span>` +
      `<span class="action-badge badge-${action}">${action}</span>`;
    seqList.appendChild(row);
  });
}

// ── Mode badge ────────────────────────────────────────────────────────────

function updateModeBadge() {
  if (state.mode === 'NODE') {
    modeBadge.textContent = 'NODE MODE';
    modeBadge.className   = 'node-mode';
  } else {
    modeBadge.textContent = 'SEQUENCE MODE';
    modeBadge.className   = 'seq-mode';
  }
}

// ── Save JSON ─────────────────────────────────────────────────────────────

function saveJSON() {
  const data = { NODES: state.nodes, SEQUENCE: state.sequence };
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
  const a    = document.createElement('a');
  a.href     = URL.createObjectURL(blob);
  a.download = state.outputFilename;
  a.click();
  URL.revokeObjectURL(a.href);
}

// ── Draw loop ─────────────────────────────────────────────────────────────

const LABEL_COLOR = '#1a1a2a';

function drawLoop() {
  const cw = canvas.width;
  const ch = canvas.height;

  ctx.fillStyle = '#f0f0f0';
  ctx.fillRect(0, 0, cw, ch);

  // Background image
  if (state.bgImage) {
    const { sx, sy } = imgToScreen(0, 0, state.view);
    ctx.drawImage(
      state.bgImage,
      sx, sy,
      state.imgW * state.view.zoom,
      state.imgH * state.view.zoom
    );
    drawGrid(ctx, state.imgW, state.imgH, state.view);
  }

  // Sequence path lines
  const validSeq = state.sequence.filter(e => state.nodes[e.node]);
  if (validSeq.length > 1) {
    ctx.beginPath();
    ctx.strokeStyle = COLORS.sequence_line;
    ctx.lineWidth   = 2;
    ctx.setLineDash([]);
    validSeq.forEach(({ node }, i) => {
      const pt = state.nodes[node];
      const { sx, sy } = imgToScreen(pt.x, pt.y, state.view);
      if (i === 0) ctx.moveTo(sx, sy); else ctx.lineTo(sx, sy);
    });
    ctx.stroke();
  }

  // Step index numbers along path
  validSeq.forEach(({ node }, i) => {
    const pt = state.nodes[node];
    const { sx, sy } = imgToScreen(pt.x, pt.y, state.view);
    ctx.fillStyle    = COLORS.sequence_line;
    ctx.font         = '11px monospace';
    ctx.textAlign    = 'left';
    ctx.textBaseline = 'bottom';
    ctx.fillText(String(i), sx + DOT_RADIUS + 1, sy - 2);
  });

  // Nodes
  for (const [id, pt] of Object.entries(state.nodes)) {
    const { sx, sy } = imgToScreen(pt.x, pt.y, state.view);
    const col = dotColorForType(pt.type);

    // Sequence mode: highlight ring for nodes already in sequence
    if (state.mode === 'SEQUENCE') {
      const inSeq = state.sequence.some(e => e.node === id);
      if (inSeq) {
        ctx.beginPath();
        ctx.arc(sx, sy, DOT_RADIUS + 5, 0, Math.PI * 2);
        ctx.strokeStyle = COLORS.seq_highlight;
        ctx.lineWidth   = 1.5;
        ctx.stroke();
      }
      // Hover ring
      if (id === state.hoveredNode) {
        ctx.beginPath();
        ctx.arc(sx, sy, DOT_RADIUS + 9, 0, Math.PI * 2);
        ctx.strokeStyle = 'rgba(255,255,255,0.7)';
        ctx.lineWidth   = 1.5;
        ctx.stroke();
      }
    }

    // Dot
    ctx.beginPath();
    ctx.arc(sx, sy, DOT_RADIUS, 0, Math.PI * 2);
    ctx.fillStyle = col;
    ctx.fill();
    ctx.strokeStyle = '#ffffff';
    ctx.lineWidth   = 1;
    ctx.stroke();

    // Heading arrow
    drawHeadingArrow(ctx, sx, sy, pt.heading, 22, col, 2);

    // Label
    ctx.fillStyle    = LABEL_COLOR;
    ctx.font         = 'bold 11px monospace';
    ctx.textAlign    = 'left';
    ctx.textBaseline = 'middle';
    ctx.fillText(`${id} [${pt.type}] (${pt.x},${pt.y}) ${pt.heading}°`, sx + DOT_RADIUS + 3, sy);
  }

  // Pending dot (while node form is open)
  if (state.pending) {
    const { sx, sy } = imgToScreen(state.pending.x, state.pending.y, state.view);
    ctx.beginPath();
    ctx.arc(sx, sy, DOT_RADIUS + 3, 0, Math.PI * 2);
    ctx.strokeStyle = '#FFDC00';
    ctx.lineWidth   = 2;
    ctx.setLineDash([4, 3]);
    ctx.stroke();
    ctx.setLineDash([]);
  }

  // Crosshair (NODE mode only, no modal open)
  if (state.mode === 'NODE' && !nodeFormModal.open && !startupModal.open) {
    ctx.strokeStyle = 'rgba(200,40,40,0.5)';
    ctx.lineWidth   = 1;
    ctx.setLineDash([]);
    ctx.beginPath();
    ctx.moveTo(state.mouseScreen.sx, 0);
    ctx.lineTo(state.mouseScreen.sx, ch);
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(0, state.mouseScreen.sy);
    ctx.lineTo(cw, state.mouseScreen.sy);
    ctx.stroke();
  }

  drawHeadingLegend(ctx, cw);
  requestAnimationFrame(drawLoop);
}
