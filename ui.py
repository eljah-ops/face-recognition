"""Thème, typographie lisible et aperçu vidéo de Face Studio."""
import math
import tkinter as tk
from tkinter import ttk
from tkinter.font import Font, nametofont
import cv2

BG = "#edf1f8"
PAPER = "#ffffff"
INK = "#18243b"
MUTED = "#506078"
LINE = "#d7dfed"
ACCENT = "#3556df"
NAVY = "#111d36"
CYAN = "#80e6e2"
_FONT_SCALE = 1.0


def font(points, weight="normal", family="DejaVu Sans"):
    return (family, -max(1, round(points * 4 / 3 * _FONT_SCALE)), weight)


def configure_theme(root):
    global _FONT_SCALE
    probe = Font(root=root, family="DejaVu Sans", size=-100)
    _FONT_SCALE = 116 / probe.metrics("linespace")
    root.configure(background=BG)
    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
        nametofont(name, root=root).configure(family="DejaVu Sans", size=font(12)[1])
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure("TFrame", background=BG)
    style.configure("Card.TFrame", background=PAPER)
    style.configure("TLabel", background=BG, foreground=INK, font=font(12))
    style.configure("Card.TLabel", background=PAPER)
    style.configure("Muted.TLabel", foreground=MUTED)
    style.configure("CardMuted.TLabel", background=PAPER, foreground=MUTED, font=font(11))
    style.configure("Section.TLabel", background=PAPER, font=font(15, "bold"))
    style.configure("TButton", font=font(12, "bold"), padding=(14, 11), background="#e8edf8", foreground=INK, borderwidth=0, focusthickness=2, focuscolor=ACCENT)
    style.map("TButton", background=[("disabled", "#edf0f5"), ("active", "#d6e0fa")], foreground=[("disabled", "#657186")])
    style.configure("Accent.TButton", background=ACCENT, foreground="white")
    style.map("Accent.TButton", background=[("disabled", "#e3e8f5"), ("active", "#2542b7")], foreground=[("disabled", "#657186"), ("!disabled", "white")])
    style.configure("Quiet.TButton", background=PAPER, foreground=MUTED, padding=(8, 6))
    style.map("Quiet.TButton", background=[("active", "#e8edf8")])
    style.configure("Stop.TButton", background="#fae9e8", foreground="#9a332e")
    for name in ("TEntry", "TSpinbox"):
        style.configure(name, fieldbackground="#f6f8fc", foreground=INK, padding=10, bordercolor=LINE, lightcolor=LINE, darkcolor=LINE, insertcolor=INK, arrowsize=18)
        style.map(name, bordercolor=[("focus", ACCENT)], fieldbackground=[("disabled", "#edf0f5")])
    style.configure("Treeview", background=PAPER, fieldbackground=PAPER, foreground=INK, rowheight=38, borderwidth=0, font=font(12))
    style.configure("Treeview.Heading", background="#eef2fa", foreground=MUTED, font=font(11, "bold"), padding=8, relief="flat")
    style.map("Treeview", background=[("selected", "#dfe7ff")], foreground=[("selected", "#1d3385")])
    style.configure("TNotebook", background=PAPER, borderwidth=0, tabmargins=(0, 0, 0, 12))
    style.configure("TNotebook.Tab", font=font(11, "bold"), background=PAPER, foreground=MUTED, padding=(12, 10), borderwidth=0)
    style.map("TNotebook.Tab", background=[("selected", "#e7edff")], foreground=[("selected", "#2542b7")])
    style.layout("Studio.Horizontal.TProgressbar", [("Horizontal.Progressbar.trough", {"sticky": "ew", "children": [("Horizontal.Progressbar.pbar", {"side": "left", "sticky": "ns"})]})])
    style.configure("Studio.Horizontal.TProgressbar", thickness=5, background=ACCENT, troughcolor="#e8edf8", borderwidth=0)


def card(parent, padding=16):
    return tk.Frame(parent, bg=PAPER, highlightbackground=LINE, highlightthickness=1, padx=padding, pady=padding)


class ScrollPanel(ttk.Frame):
    """Panneau défilant pour garder les champs accessibles sur une petite fenêtre."""
    def __init__(self, parent):
        super().__init__(parent, style="Card.TFrame")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(self, bg=PAPER, highlightthickness=0, width=310, height=1)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.bar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.bar.grid(row=0, column=1, sticky="ns")
        self.canvas.configure(yscrollcommand=self._position)
        self.body = ttk.Frame(self.canvas, style="Card.TFrame", padding=(6, 10))
        self.window = self.canvas.create_window(0, 0, window=self.body, anchor="nw")
        self.body.bind("<Configure>", lambda event: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda event: self.canvas.itemconfigure(self.window, width=event.width))
        # Le binding de la fenêtre ne s'applique qu'aux descendants de ce panneau.
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.winfo_toplevel().bind(sequence, self._wheel, add=True)

    def _position(self, first, last):
        self.bar.set(first, last)
        if float(first) <= 0 and float(last) >= 1:
            self.bar.grid_remove()
        else:
            self.bar.grid()

    def _wheel(self, event):
        if str(event.widget).startswith(str(self) + "."):
            if self.canvas.yview() == (0.0, 1.0):
                return
            direction = -1 if event.num == 4 or getattr(event, "delta", 0) > 0 else 1
            self.canvas.yview_scroll(direction * 2, "units")
            return "break"


class VideoPanel(tk.Canvas):
    """Vidéo proportionnelle et illustration vectorielle pour l'état de veille."""
    def __init__(self, parent):
        super().__init__(parent, bg="#101b32", highlightthickness=0, height=330, width=1)
        self.frame = None
        self.photo = None
        self.bind("<Configure>", lambda event: self.render())

    def show_png(self, path):
        frame = cv2.imread(str(path))
        if frame is not None:
            self.frame = frame
            self.render()
        return self.photo

    def clear(self):
        self.frame = self.photo = None
        self.render()

    def render(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 10 or h < 10:
            return
        if self.frame is not None:
            ratio = min(w/self.frame.shape[1], h/self.frame.shape[0])
            resized = cv2.resize(self.frame, (max(1, int(self.frame.shape[1]*ratio)), max(1, int(self.frame.shape[0]*ratio))))
            ok, encoded = cv2.imencode(".png", resized)
            if ok:
                self.photo = tk.PhotoImage(data=encoded.tobytes())
                self.create_image(w/2, h/2, image=self.photo)
            return
        # Une trame discrète et des orbites encadrent le symbole du visage.
        for x in range(24, w, 32):
            for y in range(24, h, 32):
                self.create_oval(x, y, x+1, y+1, outline="#26374f")
        cx, cy = w/2, h*.44
        radius = min(85, h*.24)
        for scale, color in [(1.8, "#1b2b46"), (1.35, "#283d5c"), (1.0, "#365d80")]:
            r = radius*scale
            self.create_oval(cx-r, cy-r, cx+r, cy+r, outline=color, width=1)
        for angle in (25, 150, 265):
            x, y = cx+radius*1.35*math.cos(math.radians(angle)), cy+radius*1.35*math.sin(math.radians(angle))
            self.create_oval(x-3, y-3, x+3, y+3, fill=CYAN, outline="")
        self.create_oval(cx-radius*.28, cy-radius*.5, cx+radius*.28, cy+radius*.1, outline=CYAN, width=2)
        self.create_arc(cx-radius*.52, cy+radius*.13, cx+radius*.52, cy+radius*.76, start=0, extent=180, style="arc", outline=CYAN, width=2)
        for sx, sy in [(-1,-1), (1,-1), (-1,1), (1,1)]:
            x, y = cx+sx*radius*.7, cy+sy*radius*.7
            self.create_line(x-sx*13, y, x, y, x, y-sy*13, fill="#b6caff", width=2)
        self.create_text(20, 20, text="●  CAMÉRA EN VEILLE", anchor="nw", fill="#c1cee6", font=font(11, "bold"))
        if h > 235:
            self.create_rectangle(w/2-240, h-83, w/2+240, h-15, fill="#101b32", outline="")
            self.create_text(cx, h-63, text="Prêt à te reconnaître.", fill="#ffffff", font=font(20, "bold"))
            self.create_text(cx, h-32, text="Lance l’aperçu pour trouver le bon cadrage.", fill="#c1cee6", font=font(11))
