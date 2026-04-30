"""
coord_picker.py — Click on plant layout to collect station coordinates + AGV headings.

Usage:
    python coord_picker.py <layout.png>

Modes
-----
NODE MODE (default)
    Left-click          → pin a point; fills in-window form (ID + type + heading)
    Enter / Tab         → confirm entry and move to next field
    Left/Right arrows   → cycle TYPE options while on the TYPE field
    Escape (in form)    → cancel pending entry
    Right-click         → undo last saved node
    E                   → switch to SEQUENCE MODE

SEQUENCE MODE
    Left-click on dot   → append that node to the sequence
    Right-click         → remove last entry from sequence
    E                   → switch back to NODE MODE

Both modes
    Scroll wheel        → zoom in/out (zoom anchored to cursor)
    Middle-drag         → pan
    S                   → save JSON  (NODES + SEQUENCE)
    Q / Esc (no form)   → quit

Type options:
    waypoint    — intermediate routing point the AGV passes through
    seq_point   — stop where the AGV picks up or releases a trolley

Heading convention:
    0°   → RIGHT  (+X)
    90°  → DOWN   (+Y, Y increases downward in image space)
    180° → LEFT   (-X)
    270° → UP     (-Y)

Output format:
    {
      "NODES":    { "ID": {"x": int, "y": int, "type": str, "heading": float}, ... },
      "SEQUENCE": ["ID", "ID", ...]
    }
"""

import pygame
import sys
import json
import math
from pathlib import Path

# ── config ────────────────────────────────────────────────────────────────────
DOT_COLOR_WAYPOINT  = (255, 200,  50)   # yellow
DOT_COLOR_SEQ       = ( 80, 220, 120)   # green
CROSSHAIR_COLOR     = (255,  50,  50)
PENDING_COLOR       = (255, 220,   0)
LABEL_COLOR         = (255, 255,   0)
GRID_COLOR          = ( 60,  60,  60)
HUD_COLOR           = (200, 255, 200)
FORM_BG             = ( 20,  20,  20)
FORM_BORDER         = (120, 120, 120)
FORM_ACTIVE         = ( 80, 160, 255)
SEQ_HIGHLIGHT       = (255, 100, 200)   # magenta — sequence selection ring
FONT_SIZE           = 14
DOT_RADIUS          = 6
GRID_STEP           = 100
ZOOM_STEP           = 0.15
ZOOM_MIN            = 0.1
ZOOM_MAX            = 8.0
SEQ_HIT_RADIUS      = 18   # click tolerance for sequence picking (screen px)

TYPE_OPTIONS = ["waypoint", "seq_point"]

MODE_NODE = "NODE"
MODE_SEQ  = "SEQUENCE"
# ─────────────────────────────────────────────────────────────────────────────


def img_to_screen(ix, iy, offset, zoom):
    return (int(ix * zoom + offset[0]), int(iy * zoom + offset[1]))


def screen_to_img(sx, sy, offset, zoom):
    return ((sx - offset[0]) / zoom, (sy - offset[1]) / zoom)


def draw_grid(surf, img_w, img_h, offset, zoom, font):
    step = GRID_STEP
    x = 0
    while x <= img_w:
        sx, _ = img_to_screen(x, 0, offset, zoom)
        pygame.draw.line(surf, GRID_COLOR, (sx, 0), (sx, surf.get_height()))
        if x % (step * 5) == 0:
            surf.blit(font.render(str(x), True, GRID_COLOR), (sx + 2, 2))
        x += step
    y = 0
    while y <= img_h:
        _, sy = img_to_screen(0, y, offset, zoom)
        pygame.draw.line(surf, GRID_COLOR, (0, sy), (surf.get_width(), sy))
        if y % (step * 5) == 0:
            surf.blit(font.render(str(y), True, GRID_COLOR), (2, sy + 2))
        y += step


def draw_heading_arrow(surf, cx, cy, heading_deg, length, color, width=2):
    rad = math.radians(heading_deg)
    tx  = cx + int(length * math.cos(rad))
    ty  = cy + int(length * math.sin(rad))
    bx  = cx + int((length * 0.35) * math.cos(rad))
    by  = cy + int((length * 0.35) * math.sin(rad))
    pygame.draw.line(surf, color, (cx, cy), (tx, ty), width)
    perp = math.radians(heading_deg + 90)
    hw   = max(4, length // 8)
    p1   = (tx, ty)
    p2   = (bx + int(hw * math.cos(perp)), by + int(hw * math.sin(perp)))
    p3   = (bx - int(hw * math.cos(perp)), by - int(hw * math.sin(perp)))
    pygame.draw.polygon(surf, color, [p1, p2, p3])


def draw_heading_legend(surf, font_bold):
    sw = surf.get_width()
    cx, cy  = sw - 80, 80
    r_outer = 48
    r_label = 66

    pygame.draw.circle(surf, (20, 20, 20), (cx, cy), r_outer + 18)
    pygame.draw.circle(surf, (60, 60, 60), (cx, cy), r_outer + 18, 1)

    headings = [
        (  0, "0",   (100, 220, 100)),
        ( 90, "90",  (100, 180, 255)),
        (180, "180", (255, 160,  60)),
        (270, "270", (220, 100, 220)),
    ]
    for deg, label, color in headings:
        draw_heading_arrow(surf, cx, cy, deg, r_outer, color, width=2)
        rad = math.radians(deg)
        lx  = cx + int(r_label * math.cos(rad))
        ly  = cy + int(r_label * math.sin(rad))
        lbl = font_bold.render(label, True, color)
        surf.blit(lbl, lbl.get_rect(center=(lx, ly)))

    pygame.draw.circle(surf, (255, 255, 255), (cx, cy), 4)
    title = font_bold.render("HDG", True, (180, 180, 180))
    surf.blit(title, title.get_rect(center=(cx, cy - r_outer - 10)))


def dot_color_for_type(pt_type):
    return DOT_COLOR_SEQ if pt_type == "seq_point" else DOT_COLOR_WAYPOINT


def draw_form(surf, font, font_bold, anchor_xy, field, id_buf, type_idx, hdg_buf):
    sw, sh = surf.get_size()
    fw, fh = 310, 110
    px, py = anchor_xy
    fx = min(px + 14, sw - fw - 4)
    fy = min(py + 14, sh - fh - 30)

    shadow = pygame.Surface((fw + 4, fh + 4), pygame.SRCALPHA)
    shadow.fill((0, 0, 0, 120))
    surf.blit(shadow, (fx - 2, fy - 2))

    pygame.draw.rect(surf, FORM_BG,     (fx, fy, fw, fh), border_radius=6)
    pygame.draw.rect(surf, FORM_BORDER, (fx, fy, fw, fh), 1, border_radius=6)

    row_h        = 28
    labels       = ["ID :", "TYPE:", "HDG:"]
    current_type = TYPE_OPTIONS[type_idx]

    for i, lbl_text in enumerate(labels):
        row_y      = fy + 6 + i * row_h
        active     = (i == field)
        border_col = FORM_ACTIVE if active else FORM_BORDER

        surf.blit(font_bold.render(lbl_text, True, (200, 200, 200)), (fx + 6, row_y + 4))

        box_x = fx + 52
        box_w = fw - 58
        pygame.draw.rect(surf, (35, 35, 35), (box_x, row_y, box_w, row_h - 4), border_radius=3)
        pygame.draw.rect(surf, border_col,   (box_x, row_y, box_w, row_h - 4), 1, border_radius=3)

        if i == 0:
            display = id_buf if id_buf else "e.g. VMI-1"
            color   = (255, 255, 255) if id_buf else (90, 90, 90)
            surf.blit(font.render(display, True, color), (box_x + 5, row_y + 5))
            if active:
                cx_pos = box_x + 5 + font.size(id_buf)[0] + 1
                if (pygame.time.get_ticks() // 500) % 2 == 0:
                    pygame.draw.line(surf, (255, 255, 255),
                                     (cx_pos, row_y + 4), (cx_pos, row_y + row_h - 8), 1)

        elif i == 1:
            type_color = dot_color_for_type(current_type)
            surf.blit(font.render(f"◄ {current_type} ►", True, type_color), (box_x + 5, row_y + 5))

        else:
            display = hdg_buf if hdg_buf else "0/90/180/270"
            color   = (255, 255, 255) if hdg_buf else (90, 90, 90)
            surf.blit(font.render(display, True, color), (box_x + 5, row_y + 5))
            if active:
                cx_pos = box_x + 5 + font.size(hdg_buf)[0] + 1
                if (pygame.time.get_ticks() // 500) % 2 == 0:
                    pygame.draw.line(surf, (255, 255, 255),
                                     (cx_pos, row_y + 4), (cx_pos, row_y + row_h - 8), 1)

    if field == 0:
        hint = "Enter=next  Esc=cancel"
    elif field == 1:
        hint = "◄/► or Tab=cycle  Enter=next  Esc=cancel"
    else:
        hint = "Enter=save  Esc=cancel"
    surf.blit(font.render(hint, True, (120, 120, 120)), (fx + 6, fy + fh - 14))


def draw_sequence_panel(surf, font, font_bold, sequence):
    """Right-side panel showing the current sequence list."""
    sw, sh = surf.get_size()
    panel_w = 220
    row_h   = 18
    max_rows = (sh - 80) // row_h
    visible  = sequence[-max_rows:] if len(sequence) > max_rows else sequence
    offset_i = len(sequence) - len(visible)

    panel_h = row_h * len(visible) + 40
    px = sw - panel_w - 100   # leave room for heading legend
    py = 10

    bg = pygame.Surface((panel_w, panel_h), pygame.SRCALPHA)
    bg.fill((10, 10, 10, 180))
    surf.blit(bg, (px, py))
    pygame.draw.rect(surf, SEQ_HIGHLIGHT, (px, py, panel_w, panel_h), 1, border_radius=4)

    title = font_bold.render(f"SEQUENCE ({len(sequence)})", True, SEQ_HIGHLIGHT)
    surf.blit(title, (px + 6, py + 4))

    for i, sid in enumerate(visible):
        idx   = offset_i + i
        label = font.render(f"{idx:>3}. {sid}", True, (220, 220, 220))
        surf.blit(label, (px + 6, py + 24 + i * row_h))


def find_node_at(sx, sy, nodes, offset, zoom):
    """Return the node ID closest to screen pos (sx, sy), or None if none within SEQ_HIT_RADIUS."""
    best_id   = None
    best_dist = SEQ_HIT_RADIUS + 1
    for sid, pt in nodes.items():
        nx, ny = img_to_screen(pt["x"], pt["y"], offset, zoom)
        d = math.hypot(sx - nx, sy - ny)
        if d < best_dist:
            best_dist = d
            best_id   = sid
    return best_id


def filename_screen(screen, font, font_bold, clock):
    """Blocking startup screen — returns the chosen output filename."""
    buf = ""
    while True:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.quit(); sys.exit()
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    pygame.quit(); sys.exit()
                elif event.key == pygame.K_BACKSPACE:
                    buf = buf[:-1]
                elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                    if buf.strip():
                        return buf.strip()
                else:
                    ch = event.unicode
                    if ch and ch.isprintable():
                        buf += ch

        sw, sh = screen.get_size()
        screen.fill((20, 20, 28))

        title = font_bold.render("Coord Picker — Output File", True, (200, 255, 200))
        screen.blit(title, title.get_rect(center=(sw // 2, sh // 2 - 60)))

        prompt = font.render("Enter output filename (must include .json):", True, (180, 180, 180))
        screen.blit(prompt, prompt.get_rect(center=(sw // 2, sh // 2 - 20)))

        bw, bh = 400, 34
        bx, by = sw // 2 - bw // 2, sh // 2 + 10
        pygame.draw.rect(screen, (35, 35, 35), (bx, by, bw, bh), border_radius=4)
        pygame.draw.rect(screen, FORM_ACTIVE,  (bx, by, bw, bh), 1, border_radius=4)

        display = buf if buf else "e.g. my_layout.json"
        color   = (255, 255, 255) if buf else (80, 80, 80)
        screen.blit(font.render(display, True, color), (bx + 8, by + 8))

        if buf and (pygame.time.get_ticks() // 500) % 2 == 0:
            cx_pos = bx + 8 + font.size(buf)[0] + 1
            pygame.draw.line(screen, (255, 255, 255), (cx_pos, by + 6), (cx_pos, by + bh - 6), 1)

        hint = font.render("Enter=confirm   Esc=quit", True, (100, 100, 100))
        screen.blit(hint, hint.get_rect(center=(sw // 2, by + bh + 16)))

        pygame.display.flip()
        clock.tick(60)


def load_existing(output_file):
    """Load NODES and SEQUENCE from an existing file, or return empty dicts/list."""
    p = Path(output_file)
    if p.exists():
        try:
            with open(p) as f:
                data = json.load(f)
            nodes    = data.get("NODES", {})
            sequence = data.get("SEQUENCE", [])
            print(f"[LOADED] {output_file}  ({len(nodes)} nodes, {len(sequence)} seq entries)")
            return nodes, sequence
        except Exception as e:
            print(f"[WARN] Could not load {output_file}: {e}")
    return {}, []


def main():
    if len(sys.argv) < 2:
        print("Usage: python coord_picker.py <layout.png>")
        sys.exit(1)

    img_path = Path(sys.argv[1])
    if not img_path.exists():
        print(f"File not found: {img_path}")
        sys.exit(1)

    pygame.init()
    pygame.key.set_repeat(400, 40)
    screen = pygame.display.set_mode((1280, 720), pygame.RESIZABLE)
    pygame.display.set_caption(f"Coord Picker — {img_path.name}")
    font      = pygame.font.SysFont("monospace", FONT_SIZE)
    font_bold = pygame.font.SysFont("monospace", FONT_SIZE + 2, bold=True)
    clock = pygame.time.Clock()

    output_file = filename_screen(screen, font, font_bold, clock)
    pygame.display.set_caption(f"Coord Picker — {img_path.name}  →  {output_file}")

    # Load existing data if file already exists
    nodes, sequence = load_existing(output_file)

    raw_img = pygame.image.load(str(img_path)).convert()
    img_w, img_h = raw_img.get_size()

    sw, sh = screen.get_size()
    zoom   = min(sw / img_w, sh / img_h)
    offset = [sw / 2 - img_w * zoom / 2, sh / 2 - img_h * zoom / 2]

    mode     = MODE_NODE
    pending  = None   # (ix, iy) while form open
    field    = 0
    type_idx = 0
    id_buf   = ""
    hdg_buf  = ""

    panning          = False
    pan_start        = (0, 0)
    pan_offset_start = [0, 0]

    while True:
        mouse_sx, mouse_sy = pygame.mouse.get_pos()
        mouse_ix, mouse_iy = screen_to_img(mouse_sx, mouse_sy, offset, zoom)
        mouse_ix = max(0, min(img_w, mouse_ix))
        mouse_iy = max(0, min(img_h, mouse_iy))

        for event in pygame.event.get():

            if event.type == pygame.QUIT:
                pygame.quit(); sys.exit()

            elif event.type == pygame.KEYDOWN:

                # ── node mode with form open ───────────────────────────────
                if mode == MODE_NODE and pending is not None:
                    if event.key == pygame.K_ESCAPE:
                        pending = None; id_buf = ""; hdg_buf = ""; type_idx = 0; field = 0

                    elif event.key == pygame.K_BACKSPACE:
                        if field == 0:   id_buf  = id_buf[:-1]
                        elif field == 2: hdg_buf = hdg_buf[:-1]

                    elif event.key in (pygame.K_LEFT, pygame.K_RIGHT) and field == 1:
                        delta    = 1 if event.key == pygame.K_RIGHT else -1
                        type_idx = (type_idx + delta) % len(TYPE_OPTIONS)

                    elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_TAB):
                        if field == 0:
                            if id_buf.strip():
                                field = 1
                        elif field == 1:
                            if event.key == pygame.K_TAB:
                                type_idx = (type_idx + 1) % len(TYPE_OPTIONS)
                            else:
                                field = 2
                        else:
                            sid = id_buf.strip()
                            try:    hdg = float(hdg_buf.strip()) % 360
                            except: hdg = 0.0
                            if sid:
                                ix, iy  = pending
                                pt_type = TYPE_OPTIONS[type_idx]
                                nodes[sid] = {"x": ix, "y": iy, "type": pt_type, "heading": hdg}
                                print(f"  Saved node: {sid} = ({ix}, {iy}, {pt_type}, {hdg}deg)")
                            pending = None; id_buf = ""; hdg_buf = ""; type_idx = 0; field = 0

                    else:
                        ch = event.unicode
                        if ch and ch.isprintable():
                            if field == 0:
                                id_buf += ch
                            elif field == 2 and ch in "0123456789.-":
                                hdg_buf += ch

                # ── no form open (both modes share these) ──────────────────
                else:
                    if event.key in (pygame.K_q, pygame.K_ESCAPE):
                        pygame.quit(); sys.exit()

                    elif event.key == pygame.K_e:
                        mode = MODE_SEQ if mode == MODE_NODE else MODE_NODE
                        print(f"  Switched to {mode} mode")

                    elif event.key == pygame.K_s:
                        data = {"NODES": nodes, "SEQUENCE": sequence}
                        with open(output_file, "w") as f:
                            json.dump(data, f, indent=2)
                        print(f"\n[SAVED] {output_file}  ({len(nodes)} nodes, {len(sequence)} seq entries)")

            elif event.type == pygame.MOUSEBUTTONDOWN:

                if event.button == 1:
                    if mode == MODE_NODE:
                        if pending is not None:
                            pending = None; id_buf = ""; hdg_buf = ""; type_idx = 0; field = 0
                        else:
                            pending = (int(mouse_ix), int(mouse_iy))
                            field = 0; id_buf = ""; hdg_buf = ""; type_idx = 0
                    else:  # SEQUENCE mode — click to append nearest node
                        hit = find_node_at(mouse_sx, mouse_sy, nodes, offset, zoom)
                        if hit:
                            sequence.append(hit)
                            print(f"  Seq append: {hit}  (total {len(sequence)})")

                elif event.button == 3:
                    if mode == MODE_NODE:
                        if pending is not None:
                            pending = None; id_buf = ""; hdg_buf = ""; type_idx = 0; field = 0
                        elif nodes:
                            removed = list(nodes.keys())[-1]
                            del nodes[removed]
                            print(f"  Removed node: {removed}")
                    else:  # SEQUENCE mode — remove last entry
                        if sequence:
                            removed = sequence.pop()
                            print(f"  Seq removed last: {removed}  (total {len(sequence)})")

                elif event.button == 2:
                    panning = True
                    pan_start = event.pos
                    pan_offset_start = offset[:]

                elif event.button == 4:
                    factor   = 1 + ZOOM_STEP
                    new_zoom = min(zoom * factor, ZOOM_MAX)
                    offset[0] = mouse_sx - mouse_ix * new_zoom
                    offset[1] = mouse_sy - mouse_iy * new_zoom
                    zoom = new_zoom

                elif event.button == 5:
                    factor   = 1 - ZOOM_STEP
                    new_zoom = max(zoom * factor, ZOOM_MIN)
                    offset[0] = mouse_sx - mouse_ix * new_zoom
                    offset[1] = mouse_sy - mouse_iy * new_zoom
                    zoom = new_zoom

            elif event.type == pygame.MOUSEBUTTONUP:
                if event.button == 2:
                    panning = False

            elif event.type == pygame.MOUSEMOTION:
                if panning:
                    dx = event.pos[0] - pan_start[0]
                    dy = event.pos[1] - pan_start[1]
                    offset[0] = pan_offset_start[0] + dx
                    offset[1] = pan_offset_start[1] + dy

            elif event.type == pygame.VIDEORESIZE:
                screen = pygame.display.set_mode(event.size, pygame.RESIZABLE)

        # ── draw ──────────────────────────────────────────────────────────────
        screen.fill((30, 30, 30))

        scaled = pygame.transform.scale(raw_img, (int(img_w * zoom), int(img_h * zoom)))
        screen.blit(scaled, (int(offset[0]), int(offset[1])))

        draw_grid(screen, img_w, img_h, offset, zoom, font)

        # Draw sequence path lines first (below dots)
        if sequence:
            seq_screen = []
            for sid in sequence:
                if sid in nodes:
                    pt = nodes[sid]
                    seq_screen.append(img_to_screen(pt["x"], pt["y"], offset, zoom))
            if len(seq_screen) > 1:
                pygame.draw.lines(screen, SEQ_HIGHLIGHT, False, seq_screen, 2)
            # Number each step along the path
            for i, (sx2, sy2) in enumerate(seq_screen):
                idx_lbl = font.render(str(i), True, SEQ_HIGHLIGHT)
                screen.blit(idx_lbl, (sx2 + DOT_RADIUS + 1, sy2 - FONT_SIZE - 2))

        # Draw nodes
        for sid, pt in nodes.items():
            sx2, sy2 = img_to_screen(pt["x"], pt["y"], offset, zoom)
            dot_col = dot_color_for_type(pt["type"])

            # Highlight ring in sequence mode for nodes in sequence
            if mode == MODE_SEQ and sid in sequence:
                pygame.draw.circle(screen, SEQ_HIGHLIGHT, (sx2, sy2), DOT_RADIUS + 4, 2)

            pygame.draw.circle(screen, dot_col, (sx2, sy2), DOT_RADIUS)
            pygame.draw.circle(screen, (255, 255, 255), (sx2, sy2), DOT_RADIUS, 1)
            draw_heading_arrow(screen, sx2, sy2, pt["heading"], 22, dot_col, width=2)
            lbl = font_bold.render(
                f'{sid} [{pt["type"]}] ({pt["x"]},{pt["y"]}) {pt["heading"]}deg',
                True, LABEL_COLOR)
            screen.blit(lbl, (sx2 + DOT_RADIUS + 2, sy2 - FONT_SIZE))

        # Hover highlight in sequence mode
        if mode == MODE_SEQ and pending is None:
            hit = find_node_at(mouse_sx, mouse_sy, nodes, offset, zoom)
            if hit:
                pt = nodes[hit]
                hx, hy = img_to_screen(pt["x"], pt["y"], offset, zoom)
                pygame.draw.circle(screen, (255, 255, 255), (hx, hy), DOT_RADIUS + 6, 2)

        if pending is not None:
            psx, psy = img_to_screen(pending[0], pending[1], offset, zoom)
            pygame.draw.circle(screen, PENDING_COLOR, (psx, psy), DOT_RADIUS + 2, 2)
            try:
                preview_hdg = float(hdg_buf) % 360
                draw_heading_arrow(screen, psx, psy, preview_hdg, 28, PENDING_COLOR, width=2)
            except ValueError:
                pass
            draw_form(screen, font, font_bold, (psx, psy), field, id_buf, type_idx, hdg_buf)

        sw, sh = screen.get_size()

        if pending is None and mode == MODE_NODE:
            pygame.draw.line(screen, CROSSHAIR_COLOR, (mouse_sx, 0), (mouse_sx, sh), 1)
            pygame.draw.line(screen, CROSSHAIR_COLOR, (0, mouse_sy), (sw, mouse_sy), 1)

        draw_heading_legend(screen, font_bold)

        # Sequence panel (right side, above heading legend)
        draw_sequence_panel(screen, font, font_bold, sequence)

        # Type colour legend (top-left)
        lx, ly = 12, 12
        for opt in TYPE_OPTIONS:
            col = dot_color_for_type(opt)
            pygame.draw.circle(screen, col, (lx + DOT_RADIUS, ly + DOT_RADIUS), DOT_RADIUS)
            screen.blit(font.render(opt, True, col), (lx + DOT_RADIUS * 2 + 4, ly))
            ly += FONT_SIZE + 6

        # Mode badge
        mode_col  = (100, 200, 255) if mode == MODE_NODE else SEQ_HIGHLIGHT
        mode_surf = font_bold.render(f"[ {mode} MODE ]", True, mode_col)
        screen.blit(mode_surf, (sw // 2 - mode_surf.get_width() // 2, 6))

        # HUD bar
        if mode == MODE_NODE:
            hud_right = "[E]=seq mode  [S]ave  [Q]uit  RClick=undo node  Scroll=zoom  MidDrag=pan"
        else:
            hud_right = "[E]=node mode  [S]ave  [Q]uit  LClick=add to seq  RClick=remove last"
        hud = font_bold.render(
            f"  ({int(mouse_ix)}, {int(mouse_iy)})   zoom:{zoom:.2f}x   "
            f"nodes:{len(nodes)}  seq:{len(sequence)}   {hud_right}",
            True, HUD_COLOR)
        pygame.draw.rect(screen, (0, 0, 0), (0, sh - FONT_SIZE - 6, sw, FONT_SIZE + 6))
        screen.blit(hud, (4, sh - FONT_SIZE - 4))

        pygame.display.flip()
        clock.tick(60)


if __name__ == "__main__":
    main()
