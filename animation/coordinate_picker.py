"""
coord_picker.py — Click on plant layout to collect station coordinates + AGV headings.

Usage:
    python coord_picker.py <layout.png>

Controls:
    Left-click          → pin a point; fills in-window form (ID + heading)
    Enter / Tab         → confirm entry and move to next field (ID → heading → save)
    Escape (in form)    → cancel pending entry
    Right-click         → undo last saved point
    Scroll wheel        → zoom in/out (zoom anchored to cursor)
    Middle-drag         → pan
    S (no form open)    → save coords.json
    Q / Esc (no form)   → quit

Heading convention:
    0°   → RIGHT  (+X)
    90°  → DOWN   (+Y, Y increases downward in image space)
    180° → LEFT   (-X)
    270° → UP     (-Y)

Output (coords.json):
    { "STATION_ID": {"x": int, "y": int, "heading": float}, ... }
"""

import pygame
import sys
import json
import math
from pathlib import Path

# ── config ────────────────────────────────────────────────────────────────────
CROSSHAIR_COLOR = (255,  50,  50)
DOT_COLOR       = (255,  50,  50)
PENDING_COLOR   = (255, 220,   0)
LABEL_COLOR     = (255, 255,   0)
GRID_COLOR      = ( 60,  60,  60)
HUD_COLOR       = (200, 255, 200)
FORM_BG         = ( 20,  20,  20)
FORM_BORDER     = (120, 120, 120)
FORM_ACTIVE     = ( 80, 160, 255)
FONT_SIZE       = 14
DOT_RADIUS      = 6
GRID_STEP       = 100
ZOOM_STEP       = 0.15
ZOOM_MIN        = 0.1
ZOOM_MAX        = 8.0
OUTPUT_FILE     = "coords.json"
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


def draw_form(surf, font, font_bold, anchor_xy, field, id_buf, hdg_buf):
    sw, sh = surf.get_size()
    fw, fh = 310, 80
    px, py = anchor_xy
    fx = min(px + 14, sw - fw - 4)
    fy = min(py + 14, sh - fh - 30)

    shadow = pygame.Surface((fw + 4, fh + 4), pygame.SRCALPHA)
    shadow.fill((0, 0, 0, 120))
    surf.blit(shadow, (fx - 2, fy - 2))

    pygame.draw.rect(surf, FORM_BG,     (fx, fy, fw, fh), border_radius=6)
    pygame.draw.rect(surf, FORM_BORDER, (fx, fy, fw, fh), 1, border_radius=6)

    lh      = fh // 2 - 2
    labels  = ["ID :", "HDG:"]
    buffers = [id_buf, hdg_buf]
    hints   = ["e.g. VMI-1", "0/90/180/270"]

    for i, (lbl_text, buf, hint) in enumerate(zip(labels, buffers, hints)):
        row_y      = fy + 6 + i * lh
        active     = (i == field)
        border_col = FORM_ACTIVE if active else FORM_BORDER

        surf.blit(font_bold.render(lbl_text, True, (200, 200, 200)), (fx + 6, row_y + 3))

        box_x = fx + 46
        box_w = fw - 52
        pygame.draw.rect(surf, (35, 35, 35), (box_x, row_y, box_w, lh - 4), border_radius=3)
        pygame.draw.rect(surf, border_col,   (box_x, row_y, box_w, lh - 4), 1, border_radius=3)

        display = buf if buf else hint
        color   = (255, 255, 255) if buf else (90, 90, 90)
        surf.blit(font.render(display, True, color), (box_x + 5, row_y + 3))

        if active:
            cx_pos = box_x + 5 + font.size(buf)[0] + 1
            if (pygame.time.get_ticks() // 500) % 2 == 0:
                pygame.draw.line(surf, (255, 255, 255),
                                 (cx_pos, row_y + 3), (cx_pos, row_y + lh - 7), 1)

    hint_text = "Enter=next  Esc=cancel" if field == 0 else "Enter=save  Esc=cancel"
    surf.blit(font.render(hint_text, True, (120, 120, 120)), (fx + 6, fy + fh - 14))


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

    raw_img = pygame.image.load(str(img_path)).convert()
    img_w, img_h = raw_img.get_size()

    sw, sh = screen.get_size()
    zoom   = min(sw / img_w, sh / img_h)
    offset = [sw / 2 - img_w * zoom / 2, sh / 2 - img_h * zoom / 2]

    points  = {}     # {id: {"x": int, "y": int, "heading": float}}
    pending = None   # (ix, iy) image coords while form is open
    field   = 0      # 0=ID, 1=heading
    id_buf  = ""
    hdg_buf = ""

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

                if pending is not None:
                    if event.key == pygame.K_ESCAPE:
                        pending = None; id_buf = ""; hdg_buf = ""; field = 0

                    elif event.key == pygame.K_BACKSPACE:
                        if field == 0: id_buf  = id_buf[:-1]
                        else:          hdg_buf = hdg_buf[:-1]

                    elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_TAB):
                        if field == 0:
                            if id_buf.strip():
                                field = 1
                        else:
                            sid = id_buf.strip()
                            try:    hdg = float(hdg_buf.strip()) % 360
                            except: hdg = 0.0
                            if sid:
                                ix, iy = pending
                                points[sid] = {"x": ix, "y": iy, "heading": hdg}
                                print(f"  Saved: {sid} = ({ix}, {iy}, {hdg}deg)")
                            pending = None; id_buf = ""; hdg_buf = ""; field = 0

                    else:
                        ch = event.unicode
                        if ch and ch.isprintable():
                            if field == 0:
                                id_buf += ch
                            elif ch in "0123456789.-":
                                hdg_buf += ch

                else:
                    if event.key in (pygame.K_q, pygame.K_ESCAPE):
                        pygame.quit(); sys.exit()
                    elif event.key == pygame.K_s:
                        with open(OUTPUT_FILE, "w") as f:
                            json.dump(points, f, indent=2)
                        print(f"\n[SAVED] {OUTPUT_FILE}")
                        print(json.dumps(points, indent=2))

            elif event.type == pygame.MOUSEBUTTONDOWN:

                if event.button == 1:
                    if pending is not None:
                        pending = None; id_buf = ""; hdg_buf = ""; field = 0
                    else:
                        pending = (int(mouse_ix), int(mouse_iy))
                        field = 0; id_buf = ""; hdg_buf = ""

                elif event.button == 3:
                    if pending is not None:
                        pending = None; id_buf = ""; hdg_buf = ""; field = 0
                    elif points:
                        removed = list(points.keys())[-1]
                        del points[removed]
                        print(f"  Removed: {removed}")

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

        for sid, pt in points.items():
            sx, sy = img_to_screen(pt["x"], pt["y"], offset, zoom)
            pygame.draw.circle(screen, DOT_COLOR, (sx, sy), DOT_RADIUS)
            pygame.draw.circle(screen, (255, 255, 255), (sx, sy), DOT_RADIUS, 1)
            draw_heading_arrow(screen, sx, sy, pt["heading"], 22, DOT_COLOR, width=2)
            lbl = font_bold.render(
                f'{sid} ({pt["x"]},{pt["y"]}) {pt["heading"]}deg', True, LABEL_COLOR)
            screen.blit(lbl, (sx + DOT_RADIUS + 2, sy - FONT_SIZE))

        if pending is not None:
            psx, psy = img_to_screen(pending[0], pending[1], offset, zoom)
            pygame.draw.circle(screen, PENDING_COLOR, (psx, psy), DOT_RADIUS + 2, 2)
            try:
                preview_hdg = float(hdg_buf) % 360
                draw_heading_arrow(screen, psx, psy, preview_hdg, 28, PENDING_COLOR, width=2)
            except ValueError:
                pass
            draw_form(screen, font, font_bold, (psx, psy), field, id_buf, hdg_buf)

        sw, sh = screen.get_size()
        if pending is None:
            pygame.draw.line(screen, CROSSHAIR_COLOR, (mouse_sx, 0), (mouse_sx, sh), 1)
            pygame.draw.line(screen, CROSSHAIR_COLOR, (0, mouse_sy), (sw, mouse_sy), 1)

        draw_heading_legend(screen, font_bold)

        hud = font_bold.render(
            f"  ({int(mouse_ix)}, {int(mouse_iy)})   zoom:{zoom:.2f}x   "
            f"pts:{len(points)}   [S]ave  [Q]uit  RClick=undo  Scroll=zoom  MidDrag=pan",
            True, HUD_COLOR)
        pygame.draw.rect(screen, (0, 0, 0), (0, sh - FONT_SIZE - 6, sw, FONT_SIZE + 6))
        screen.blit(hud, (4, sh - FONT_SIZE - 4))

        pygame.display.flip()
        clock.tick(60)


if __name__ == "__main__":
    main()
    