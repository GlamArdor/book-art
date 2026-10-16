# Book Art – Copyright (C) 2026 Glam_Ardor
#
# This program is free software: you can redistribute it and/or modify it under the terms of the
# GNU General Public License as published by the Free Software Foundation, either version 3 of
# the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
# without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
# See the GNU General Public License for more details: <https://www.gnu.org/licenses/>.

"""Картинка в книгу: превращает изображение в текст, который рисует его на странице книги Minecraft.

Ширины знаков взяты из самой игры (1.21.8):
  * █▀▄▐▒▓ живут в ascii.png, каждый занимает 8 px и сдвигает строку на 9;
  * пустой U+3000 приходит из Unifont и сдвигает тоже на 9, поэтому клетки цветного режима
    всегда стоят ровно;
  * Брайль (Unifont) – 3 px на знак; пустые места набираются U+2007 (5 px), U+3000 (9 px) и
    пробелом (4 px), потому что пустая клетка Брайля в Unifont 16 рисует мелкие точки.
Игра переносит строку только на пробеле, а страница шириной 114 px, так что ни одна строка
картинки не выходит за 114 px.
"""
import json
import os
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageDraw, ImageEnhance, ImageOps, ImageTk

HERE = os.path.dirname(os.path.abspath(__file__))
FONT = json.load(open(os.path.join(HERE, "fontdata.json"), encoding="utf-8"))
PAPER = (233, 222, 196)  # страница книги без ресурспаков, примерно

PAGE_WIDTH = 114
PAGE_LINES = 14
PAGE_CHARS = 1024
LINE_HEIGHT = 9

SECTION = "§"
WIDE = "　"    # 9 px, пусто
FIGURE = " "  # 5 px, пусто
SPACE = " "         # 4 px; в начале строки нельзя – Quill примет за отступ

COLORS = [  # код, цвет; чёрный пишется как §r – это просто чернила книги
    ("r", (0, 0, 0)), ("1", (0, 0, 170)), ("2", (0, 170, 0)), ("3", (0, 170, 170)),
    ("4", (170, 0, 0)), ("5", (170, 0, 170)), ("6", (255, 170, 0)), ("7", (170, 170, 170)),
    ("8", (85, 85, 85)), ("9", (85, 85, 255)), ("a", (85, 255, 85)), ("b", (85, 255, 255)),
    ("c", (255, 85, 85)), ("d", (255, 85, 255)), ("e", (255, 255, 85)), ("f", (255, 255, 255)),
]
COLOR_OF = dict(COLORS)

ASCII = {ch: rows for ch, rows in FONT["ascii"].items()}
BRAILLE_HEX = FONT["unifont"]
ADVANCE = {WIDE: 9, FIGURE: 5, SPACE: 4}
for ch in ASCII:
    ADVANCE[ch] = 9
for ch in BRAILLE_HEX:
    ADVANCE[ch] = 3

# Брайль: точки 1-2-3-7 слева, 4-5-6-8 справа, сверху вниз.
BRAILLE_BIT = [[0, 3], [1, 4], [2, 5], [6, 7]]
# Где точки Брайля стоят внутри строки высотой 18 полупикселей: ряды 3, 6, 9, 12 глифа.
# Каждой точке отдан свой кусок высоты, чтобы картинка не теряла то, что между ними.
BRAILLE_BANDS = [(0.0, 4.5), (4.5, 7.5), (7.5, 10.5), (10.5, 18.0)]


def dist2(a, b):
    # Немного по-человечески: зелёный глаз видит сильнее всего.
    return 2 * (a[0] - b[0]) ** 2 + 4 * (a[1] - b[1]) ** 2 + 3 * (a[2] - b[2]) ** 2


def nearest_color(rgb, allow_white=True):
    best = None
    for code, c in COLORS:
        if code == "f" and not allow_white:
            continue
        d = dist2(rgb, c)
        if best is None or d < best[0]:
            best = (d, code)
    return best[1]


# ---- подготовка картинки ---------------------------------------------------------------------

def prepare(path, brightness, contrast, saturation, bg_tolerance):
    img = Image.open(path)
    img = ImageOps.exif_transpose(img).convert("RGBA")
    rgb = img.convert("RGB")
    if brightness != 1.0:
        rgb = ImageEnhance.Brightness(rgb).enhance(brightness)
    if contrast != 1.0:
        rgb = ImageEnhance.Contrast(rgb).enhance(contrast)
    if saturation != 1.0:
        rgb = ImageEnhance.Color(rgb).enhance(saturation)
    alpha = img.getchannel("A")
    if bg_tolerance > 0:
        # Фон – цвет углов картинки. Всё, что близко к нему, становится бумагой.
        src = img.convert("RGB")
        w, h = src.size
        corners = [src.getpixel((0, 0)), src.getpixel((w - 1, 0)),
                   src.getpixel((0, h - 1)), src.getpixel((w - 1, h - 1))]
        bg = tuple(sorted(c[i] for c in corners)[1] for i in range(3))
        tol = bg_tolerance * bg_tolerance * 9
        small = src.resize((min(w, 300), max(1, int(h * min(w, 300) / w))))
        mask = Image.new("L", small.size, 255)
        px, mp = small.load(), mask.load()
        for y in range(small.height):
            for x in range(small.width):
                if dist2(px[x, y], bg) < tol:
                    mp[x, y] = 0
        mask = mask.resize((w, h))
        alpha = Image.composite(alpha, Image.new("L", (w, h), 0), mask)
    out = rgb.convert("RGBA")
    out.putalpha(alpha)
    # Пустые поля по краям не нужны – картинка занимает всё, что ей дали.
    box = alpha.point(lambda v: 255 if v > 16 else 0).getbbox()
    if box:
        out = out.crop(box)
    return out


def fit(img, max_cols, max_rows, cell_w, cell_h):
    """Сколько клеток займёт картинка, сохраняя пропорции."""
    aspect = img.width / img.height
    cols = max_cols
    rows = max(1, round(cols * cell_w / aspect / cell_h))
    if rows > max_rows:
        rows = max_rows
        cols = max(1, min(max_cols, round(rows * cell_h * aspect / cell_w)))
    return cols, rows


def flatten(rgba):
    """Цвет точки на бумаге и насколько она закрашена."""
    r, g, b, a = rgba
    f = a / 255.0
    return (r * f + PAPER[0] * (1 - f), g * f + PAPER[1] * (1 - f), b * f + PAPER[2] * (1 - f)), f


# ---- цветные блоки ----------------------------------------------------------------------------

def quadrant_cover(rows):
    """Какая доля каждой четверти глифа 8x8 закрашена: [лв, пв, лн, пн]."""
    cover = []
    for qy in (0, 4):
        for qx in (0, 4):
            n = sum(1 for y in range(qy, qy + 4) for x in range(qx, qx + 4) if rows[y] & (0x80 >> x))
            cover.append(n / 16.0)
    return cover


BLOCK_GLYPHS = {ch: quadrant_cover(rows) for ch, rows in ASCII.items()}


def blocks(img, max_cols, max_rows, allow_white, shades):
    if img.width <= max_cols * 2 and img.height <= max_rows * 2:
        # Пиксель-арт, который и так помещается: пиксель картинки = четверть клетки, без размытия.
        cols, rows = (img.width + 1) // 2, (img.height + 1) // 2
        small = Image.new("RGBA", (cols * 2, rows * 2), (0, 0, 0, 0))
        small.paste(img, (0, 0))
    else:
        cols, rows = fit(img, max_cols, max_rows, 9, 9)
        small = img.resize((cols * 2, rows * 2), Image.BOX)
    px = small.load()
    candidates = []
    for ch, cover in BLOCK_GLYPHS.items():
        if not shades and ch in "▒▓":
            continue
        for code, c in COLORS:
            if code == "f" and not allow_white:
                continue
            look = [tuple(c[i] * f + PAPER[i] * (1 - f) for i in range(3)) for f in cover]
            candidates.append((ch, code, look))
    grid = []
    for row in range(rows):
        line = []
        for col in range(cols):
            target = [flatten(px[col * 2 + dx, row * 2 + dy])[0] for dy in (0, 1) for dx in (0, 1)]
            best = (sum(dist2(t, PAPER) for t in target), None, None)
            for ch, code, look in candidates:
                e = sum(dist2(t, l) for t, l in zip(target, look))
                if e < best[0]:
                    best = (e, ch, code)
            line.append(None if best[1] is None else (best[1], best[2]))
        grid.append(line)
    return grid, cols


# ---- Брайль ----------------------------------------------------------------------------------

def braille(img, max_cols, max_rows, invert, dither, colored, allow_white, threshold):
    cols, rows = fit(img, max_cols, max_rows, 6, 18)
    # Картинка, натянутая на страницу в полупикселях игры.
    w, h = cols * 6, rows * 18
    big = img.resize((w, h), Image.BOX)
    dots_w, dots_h = cols * 2, rows * 4
    value = [[0.0] * dots_w for _ in range(dots_h)]
    tint = [[None] * dots_w for _ in range(dots_h)]
    for line in range(rows):
        for band, (y0, y1) in enumerate(BRAILLE_BANDS):
            top = int(line * 18 + y0)
            bottom = max(top + 1, int(line * 18 + y1))
            strip = big.crop((0, top, w, bottom)).resize((dots_w, 1), Image.BOX)
            sp = strip.load()
            for x in range(dots_w):
                rgb, f = flatten(sp[x, 0])
                lum = (0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]) / 255.0
                paper = (0.299 * PAPER[0] + 0.587 * PAPER[1] + 0.114 * PAPER[2]) / 255.0
                dark = max(0.0, min(1.0, (paper - lum) / paper))
                if colored:
                    # В цвете точка ставится там, где есть рисунок, а не только тёмное.
                    dark = max(dark, f * 0.85)
                value[line * 4 + band][x] = (1.0 - dark) if invert else dark
                r, g, b, a = sp[x, 0]
                tint[line * 4 + band][x] = (r, g, b)
    on = [[False] * dots_w for _ in range(dots_h)]
    for y in range(dots_h):
        for x in range(dots_w):
            v = value[y][x]
            o = v >= threshold
            on[y][x] = o
            if dither:
                err = v - (1.0 if o else 0.0)
                for dx, dy, k in ((1, 0, 7), (-1, 1, 3), (0, 1, 5), (1, 1, 1)):
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < dots_w and ny < dots_h:
                        value[ny][nx] += err * k / 16.0
    grid = []
    for line in range(rows):
        out = []
        for col in range(cols):
            bits, acc, n = 0, [0, 0, 0], 0
            for r in range(4):
                for c in range(2):
                    y, x = line * 4 + r, col * 2 + c
                    if on[y][x]:
                        bits |= 1 << BRAILLE_BIT[r][c]
                        t = tint[y][x]
                        acc = [acc[i] + t[i] for i in range(3)]
                        n += 1
            if bits == 0:
                out.append(None)
            else:
                code = nearest_color(tuple(a / n for a in acc), allow_white) if colored else "r"
                out.append((chr(0x2800 + bits), code))
        grid.append(out)
    return grid, cols


def blank_run(cells, at_start, at_end):
    """Пустые клетки Брайля (по 3 px), набранные пустыми знаками нужной ширины."""
    for ugly in range(cells + 1):
        width = 3 * (cells - ugly)
        best = None
        for wide in range(width // 9 + 1):
            for fig in range((width - 9 * wide) // 5 + 1):
                rest = width - 9 * wide - 5 * fig
                if rest % 4:
                    continue
                spaces = rest // 4
                if spaces and (at_end or (at_start and not (wide or fig))):
                    continue
                n = wide + fig + spaces
                if best is None or n < best[0]:
                    best = (n, wide, fig, spaces)
        if best:
            # Пробелы последними: так строка никогда не начинается с пробела.
            return "⠀" * ugly + WIDE * best[1] + FIGURE * best[2] + SPACE * best[3]
    return "⠀" * cells


def encode(grid, cell_width, even):
    lines = []
    for row in grid:
        last = len(row)
        if not even:
            while last and row[last - 1] is None:
                last -= 1
        text, color, col = [], "r", 0
        while col < last:
            if row[col] is None:
                run = col
                while run < last and row[run] is None:
                    run += 1
                if cell_width == 9:
                    text.append(WIDE * (run - col))
                else:
                    # В конце строки пробелы выбросит Quill, поэтому там только U+2007/U+3000.
                    text.append(blank_run(run - col, col == 0, run == last))
                col = run
                continue
            ch, code = row[col]
            if code != color:
                text.append(SECTION + code)
                color = code
            text.append(ch)
            col += 1
        lines.append("".join(text))
    while lines and not lines[-1]:
        lines.pop()
    return lines


# ---- превью ----------------------------------------------------------------------------------

def render(lines, scale=4):
    """Страница так, как её нарисует игра: 1 px игры = scale px экрана."""
    margin = 4
    img = Image.new("RGB", ((PAGE_WIDTH + margin * 2) * scale, (PAGE_LINES * LINE_HEIGHT + margin * 2) * scale), PAPER)
    d = ImageDraw.Draw(img)
    edge = (PAGE_WIDTH + margin) * scale
    d.line([(edge, 0), (edge, img.height)], fill=(200, 160, 150))
    for n, text in enumerate(lines[:PAGE_LINES]):
        x = margin * 2
        y = (margin + n * LINE_HEIGHT) * 2
        color = (0, 0, 0)
        i = 0
        while i < len(text):
            ch = text[i]
            if ch == SECTION and i + 1 < len(text):
                color = COLOR_OF.get(text[i + 1], color)
                i += 2
                continue
            half = scale // 2
            if ch in ASCII:
                for gy, bits in enumerate(ASCII[ch]):
                    for gx in range(8):
                        if bits & (0x80 >> gx):
                            X, Y = (x + gx * 2) * half, (y + gy * 2) * half
                            d.rectangle([X, Y, X + scale - 1, Y + scale - 1], fill=color)
            elif ch in BRAILLE_HEX:
                hx = BRAILLE_HEX[ch]
                for gy in range(16):
                    bits = int(hx[gy * 2:gy * 2 + 2], 16)
                    for gx in range(8):
                        if bits & (0x80 >> gx):
                            # Unifont рисуется в полпикселя, начиная со своего левого столбца (2).
                            X, Y = (x + gx - 2) * half, (y + gy - 1) * half
                            d.rectangle([X, Y, X + half - 1, Y + half - 1], fill=color)
            x += ADVANCE.get(ch, 4) * 2
            i += 1
    return img


# ---- окно ------------------------------------------------------------------------------------

class App:
    def __init__(self, root, path):
        self.root = root
        self.path = None
        self.lines = []
        self.pending = None
        root.title("Картинка в книгу")
        root.configure(padx=10, pady=10)

        self.preview = ttk.Label(root)
        self.preview.grid(row=0, column=0, rowspan=20, sticky="n", padx=(0, 12))

        side = ttk.Frame(root)
        side.grid(row=0, column=1, sticky="n")
        ttk.Button(side, text="Открыть картинку…", command=self.choose).pack(fill="x")
        self.name = ttk.Label(side, text="", wraplength=260)
        self.name.pack(fill="x", pady=(4, 10))

        self.mode = tk.StringVar(value="blocks")
        box = ttk.LabelFrame(side, text="Чем рисовать", padding=6)
        box.pack(fill="x")
        for value, label in (("blocks", "Цветные блоки (до 12×14)"),
                             ("braille", "Точки Брайля (до 74×56)"),
                             ("braille_color", "Цветные точки Брайля")):
            ttk.Radiobutton(box, text=label, value=value, variable=self.mode, command=self.changed).pack(anchor="w")

        self.size = self.slider(side, "Ширина, % страницы", 10, 100, 100)
        self.bright = self.slider(side, "Яркость", 30, 200, 100)
        self.contrast = self.slider(side, "Контраст", 30, 300, 100)
        self.satur = self.slider(side, "Насыщенность", 0, 300, 100)
        self.bg = self.slider(side, "Убрать фон (0 – не трогать)", 0, 100, 25)
        self.thresh = self.slider(side, "Порог точек (Брайль)", 5, 95, 50)

        self.invert = self.check(side, "Инвертировать (Брайль)", False)
        self.dither = self.check(side, "Дизеринг (полутона точками)", True)
        self.white = self.check(side, "Разрешить белый цвет", True)
        self.shades = self.check(side, "Полутона ▒▓ (блоки)", False)
        self.even = self.check(side, "Строки одной ширины (для центра)", True)

        self.info = ttk.Label(side, text="", wraplength=260, justify="left")
        self.info.pack(fill="x", pady=(10, 6))
        ttk.Button(side, text="Скопировать для книги", command=self.copy).pack(fill="x")
        ttk.Button(side, text="Сохранить в .txt", command=self.save).pack(fill="x", pady=(4, 0))
        hint = ("Вставка: открыть книгу с Quill, выравнивание по левому краю "
                "(или по центру, если стоит галочка «одной ширины»), Ctrl+V.")
        ttk.Label(side, text=hint, wraplength=260, foreground="#666").pack(fill="x", pady=(10, 0))

        if path:
            self.load(path)
        else:
            self.show_empty()

    def slider(self, parent, label, lo, hi, value):
        var = tk.IntVar(value=value)
        frame = ttk.Frame(parent)
        frame.pack(fill="x", pady=(8, 0))
        top = ttk.Frame(frame)
        top.pack(fill="x")
        ttk.Label(top, text=label).pack(side="left")
        shown = ttk.Label(top, text=str(value))
        shown.pack(side="right")

        def moved(_=None):
            shown.configure(text=str(var.get()))
            self.changed()
        ttk.Scale(frame, from_=lo, to=hi, orient="horizontal", length=260,
                  command=lambda v: (var.set(int(float(v))), moved())).pack(fill="x")
        frame.winfo_children()[-1].set(value)
        return var

    def check(self, parent, label, value):
        var = tk.BooleanVar(value=value)
        ttk.Checkbutton(parent, text=label, variable=var, command=self.changed).pack(anchor="w", pady=(6, 0))
        return var

    def choose(self):
        path = filedialog.askopenfilename(filetypes=[("Картинки", "*.png *.jpg *.jpeg *.webp *.gif *.bmp"), ("Все", "*.*")])
        if path:
            self.load(path)

    def load(self, path):
        self.path = path
        self.name.configure(text=os.path.basename(path))
        self.changed()

    def show_empty(self):
        self.photo = ImageTk.PhotoImage(render([]))
        self.preview.configure(image=self.photo)
        self.info.configure(text="Перетащите картинку в это окно или нажмите «Открыть картинку».")

    def changed(self):
        if self.pending:
            self.root.after_cancel(self.pending)
        self.pending = self.root.after(150, self.rebuild)

    def rebuild(self):
        self.pending = None
        if not self.path:
            return
        try:
            img = prepare(self.path, self.bright.get() / 100, self.contrast.get() / 100,
                          self.satur.get() / 100, self.bg.get())
        except Exception as e:
            messagebox.showerror("Картинка в книгу", "Не удалось открыть картинку:\n%s" % e)
            self.path = None
            return
        share = self.size.get() / 100
        mode = self.mode.get()
        if mode == "blocks":
            grid, cols = blocks(img, max(1, int(12 * share)), PAGE_LINES, self.white.get(), self.shades.get())
            self.lines = encode(grid, 9, self.even.get())
        else:
            grid, cols = braille(img, max(1, int(37 * share)), PAGE_LINES, self.invert.get(), self.dither.get(),
                                 mode == "braille_color", self.white.get(), self.thresh.get() / 100)
            self.lines = encode(grid, 3, self.even.get())
        self.photo = ImageTk.PhotoImage(render(self.lines))
        self.preview.configure(image=self.photo)
        chars = len(self.text())
        warn = "" if chars <= PAGE_CHARS else "\nБольше 1024 знаков – страница не примет. Уменьшите ширину или цвета."
        self.info.configure(text="%d строк, %d знаков из %d на странице.%s" % (len(self.lines), chars, PAGE_CHARS, warn))

    def text(self):
        return "\n".join(self.lines)

    def copy(self):
        if not self.lines:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(self.text())
        self.root.update()
        self.info.configure(text=self.info.cget("text").split("\n")[0] + "\nСкопировано.")

    def save(self):
        if not self.lines:
            return
        start = os.path.splitext(self.path)[0] + " - книга.txt"
        path = filedialog.asksaveasfilename(initialfile=os.path.basename(start), initialdir=os.path.dirname(start),
                                            defaultextension=".txt", filetypes=[("Текст", "*.txt")])
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.text() + "\n")


IMAGE_TYPES = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")


def main():
    try:
        # Своя иконка на панели задач, а не иконка Python.
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("glamardor.bookart")
    except Exception:
        pass
    root = tk.Tk()
    icon = os.path.join(HERE, "book_and_quill.ico")
    if os.path.exists(icon):
        root.iconbitmap(default=icon)
    app = App(root, sys.argv[1] if len(sys.argv) > 1 else None)
    try:
        import windnd

        def dropped(files):
            paths = [f.decode("utf-8") if isinstance(f, bytes) else f for f in files]
            images = [p for p in paths if p.lower().endswith(IMAGE_TYPES)]
            if images:
                root.after(0, app.load, images[0])
        windnd.hook_dropfiles(root, func=dropped, force_unicode=True)
    except ImportError:
        pass
    root.mainloop()


if __name__ == "__main__":
    main()
