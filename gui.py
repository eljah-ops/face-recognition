"""Face Studio Premium : interface locale et supervision du traitement vidéo."""
import argparse
import csv
import json
import tempfile
import time
from datetime import datetime
from pathlib import Path
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from uuid import uuid4

from main import ROOT, person_name
from recognition import DEFAULTS, load_settings, save_settings
from ui import BG, PAPER, INK, MUTED, ACCENT, NAVY, CYAN, card, configure_theme, VideoPanel, ScrollPanel, font


def list_people(data):
    return [(p.name, len(list(p.glob("*.png"))))
            for p in sorted((data / "faces").glob("*")) if p.is_dir()]


def archive_person(data, name):
    """Retire une personne de l'entraînement en conservant ses captures."""
    name = person_name(name)
    folder = data / "faces" / name
    if not folder.is_dir() or folder.is_symlink():
        raise RuntimeError("Dossier de personne absent ou non pris en charge.")
    archive = data / "archives" / uuid4().hex
    archive.mkdir(parents=True)
    # Invalider d'abord le modèle pour ne plus reconnaître la personne retirée.
    model = data / "model.yml"
    if model.exists():
        model.replace(archive / "model.yml")
    folder.rename(archive / name)
    return archive


class Dashboard:
    def __init__(self, root, data):
        self.root, self.data = root, data.resolve()
        self.process = None
        self.events = queue.Queue()
        self.closing = self.stopping = False
        self.preview = None
        self.history = []
        self.last_seen = {}
        self.controls = []
        self.image = None
        warning = ""
        try:
            settings = load_settings(self.data)
        except (ValueError, OSError) as error:
            settings, warning = dict(DEFAULTS), str(error)
        root.title("Face Studio · Premium")
        root.geometry("1320x960")
        root.minsize(1120, 850)
        configure_theme(root)
        body = ttk.Frame(root, padding=22)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(2, weight=1)
        header = tk.Frame(body, bg=NAVY, padx=24, pady=18)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 16))
        logo = tk.Canvas(header, width=58, height=58, bg=NAVY, highlightthickness=0)
        logo.pack(side="left", padx=(0, 18))
        logo.create_oval(1, 1, 57, 57, fill="#28416c", outline="#5874ae")
        for x, y, sx, sy in [(17,17,1,1), (41,17,-1,1), (17,41,1,-1), (41,41,-1,-1)]:
            logo.create_line(x+sx*7, y, x, y, x, y+sy*7, fill=CYAN, width=2)
        titles = tk.Frame(header, bg=NAVY)
        titles.pack(side="left")
        tk.Label(titles, text="Face Studio", bg=NAVY, fg="white", font=font(30, "bold")).pack(anchor="w")
        tk.Label(titles, text="Un regard. Une identité. Ton studio.", bg=NAVY, fg="#c4d1ea", font=font(12)).pack(anchor="w", pady=(4, 0))
        tk.Label(header, text="●  LOCAL  /  PREMIUM", bg="#24385b", fg=CYAN, padx=18, pady=12, font=font(11, "bold")).pack(side="right")
        stats = ttk.Frame(body)
        stats.grid(row=1, column=0, sticky="ew", pady=(0, 16))
        self.stat_people, self.stat_captures, self.stat_model = tk.StringVar(), tk.StringVar(), tk.StringVar()
        for column, (title, variable) in enumerate([("Personnes enregistrées", self.stat_people), ("Captures disponibles", self.stat_captures), ("État du modèle", self.stat_model)]):
            stats.columnconfigure(column, weight=1, uniform="stats")
            tile = card(stats, 12)
            tile.grid(row=0, column=column, sticky="ew", padx=(0, 12 if column < 2 else 0))
            tk.Label(tile, text=title, bg=PAPER, fg=MUTED, font=font(11)).pack(anchor="w")
            tk.Label(tile, textvariable=variable, bg=PAPER, fg=INK, font=font(23, "bold")).pack(anchor="w", pady=(2, 0))
        self.summary = tk.StringVar()
        content = ttk.Frame(body)
        content.grid(row=2, column=0, sticky="nsew")
        content.columnconfigure(0, weight=1)
        content.columnconfigure(1, minsize=380)
        content.rowconfigure(0, weight=1)
        left = ttk.Frame(content)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 16))
        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)
        camera_card = card(left, 14)
        camera_card.grid(row=0, column=0, sticky="nsew")
        camera_card.columnconfigure(0, weight=1)
        camera_card.rowconfigure(1, weight=1)
        camera_title = ttk.Frame(camera_card, style="Card.TFrame")
        camera_title.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        ttk.Label(camera_title, text="Studio en direct", style="Section.TLabel").pack(side="left")
        ttk.Label(camera_title, text="Un visage à la fois", style="CardMuted.TLabel").pack(side="right")
        self.video = VideoPanel(camera_card)
        self.video.grid(row=1, column=0, sticky="nsew")
        self.metrics = tk.StringVar(value="— FPS     ·     Netteté : —     ·     Distance : —")
        ttk.Label(camera_card, textvariable=self.metrics, style="CardMuted.TLabel").grid(row=2, column=0, sticky="w", pady=(10, 8))
        self.status = tk.StringVar(value="Commence par vérifier le cadrage avec un aperçu.")
        status_label = ttk.Label(camera_card, textvariable=self.status, style="Card.TLabel")
        status_label.grid(row=3, column=0, sticky="ew", pady=(0, 10))
        camera_card.bind("<Configure>", lambda e: status_label.configure(wraplength=max(200, e.width-32)))
        self.progress = ttk.Progressbar(camera_card, maximum=settings["samples"], style="Studio.Horizontal.TProgressbar")
        self.progress.grid(row=4, column=0, sticky="ew", pady=(0, 12))
        actions = ttk.Frame(camera_card, style="Card.TFrame")
        actions.grid(row=5, column=0, sticky="ew")
        for column, (label, command) in enumerate([("Aperçu", "apercu"), ("Entraîner", "entrainer"), ("Reconnaître", "reconnaitre")]):
            button = ttk.Button(actions, text=label, command=lambda c=command: self.start(c), style="Accent.TButton" if command == "reconnaitre" else "TButton")
            button.grid(row=0, column=column, sticky="ew", padx=(0, 6))
            actions.columnconfigure(column, weight=1)
            self.controls.append(button)
        self.stop_button = ttk.Button(actions, text="Arrêter", command=self.stop, state="disabled", style="Stop.TButton")
        self.stop_button.grid(row=0, column=3, sticky="ew")
        actions.columnconfigure(3, weight=1)
        activity = card(left, 10)
        activity.grid(row=1, column=0, sticky="ew", pady=(14, 0))
        self.activity_toggle = ttk.Button(activity, text="＋  Activité et résultats", command=self.toggle_activity, style="Quiet.TButton")
        self.activity_toggle.pack(anchor="w")
        self.activity_tabs = ttk.Notebook(activity, height=110)
        logs = ttk.Frame(self.activity_tabs, style="Card.TFrame")
        results = ttk.Frame(self.activity_tabs, style="Card.TFrame")
        self.activity_tabs.add(logs, text="Journal d’activité")
        self.activity_tabs.add(results, text="Résultats de session")
        self.log = tk.Text(logs, height=3, state="disabled", wrap="word", background=PAPER, foreground=MUTED, relief="flat", padx=8, pady=6, font=font(11))
        self.log.pack(fill="both", expand=True)
        self.append("Bienvenue. Une seule capture suffit pour créer un premier profil.\n")
        export = ttk.Button(results, text="Exporter en CSV", command=self.export, style="Quiet.TButton")
        export.pack(side="bottom", anchor="e")
        self.results = ttk.Treeview(results, columns=("time", "name", "distance"), show="headings", height=1)
        for key, title in [("time", "Heure"), ("name", "Personne"), ("distance", "Distance")]:
            self.results.heading(key, text=title)
            self.results.column(key, width=110, minwidth=70)
        self.results.pack(fill="both", expand=True)
        right = card(content, 14)
        right.grid(row=0, column=1, sticky="nsew")
        self.sidebar = ttk.Notebook(right)
        self.sidebar.pack(fill="both", expand=True)
        self.session_panel = ScrollPanel(self.sidebar)
        self.settings_panel = ScrollPanel(self.sidebar)
        session = self.session_panel.body
        advanced = self.settings_panel.body
        library = ttk.Frame(self.sidebar, style="Card.TFrame", padding=(6, 10))
        for tab, title in [(self.session_panel, "Session"), (self.settings_panel, "Réglages"), (library, "Personnes")]:
            self.sidebar.add(tab, text=title)
        self.name = tk.StringVar()
        self.variables = {key: tk.StringVar(value=str(value)) for key, value in settings.items()}
        ttk.Label(session, text="Créer un profil", style="Section.TLabel").pack(anchor="w")
        ttk.Label(session, text="Enregistre un visage pour commencer.", style="CardMuted.TLabel", wraplength=310).pack(anchor="w", pady=(8, 12))
        ttk.Label(session, text="Nom de la personne", style="Card.TLabel").pack(anchor="w", pady=(0, 8))
        self.name_entry = ttk.Entry(session, textvariable=self.name, font=font(13))
        self.name_entry.pack(fill="x", pady=(0, 12))
        self.controls.append(self.name_entry)
        ttk.Label(session, text="Nombre de captures", style="Card.TLabel").pack(anchor="w", pady=(0, 8))
        self.capture_input = ttk.Spinbox(session, textvariable=self.variables["samples"], from_=1, to=500, increment=1, font=font(13), width=8)
        self.capture_input.pack(fill="x")
        self.controls.append(self.capture_input)
        ttk.Label(session, text="Dès 1 capture · Jusqu’à 500", style="CardMuted.TLabel").pack(anchor="w", pady=(8, 12))
        self.record_button = ttk.Button(session, text="Enregistrer le visage", command=lambda: self.start("enregistrer"), style="Accent.TButton")
        self.record_button.pack(fill="x")
        self.controls.append(self.record_button)
        steps = tk.Frame(session, bg="#eef3ff", padx=14, pady=12)
        steps.pack(fill="x", pady=(12, 0))
        for number, title in [("01", "Enregistre une personne"), ("02", "Entraîne le modèle"), ("03", "Lance la reconnaissance")]:
            row = tk.Frame(steps, bg="#eef3ff")
            row.pack(fill="x", pady=3)
            tk.Label(row, text=number, bg="#eef3ff", fg=ACCENT, font=font(11, "bold")).pack(side="left", padx=(0, 10))
            tk.Label(row, text=title, bg="#eef3ff", fg=INK, font=font(11)).pack(side="left")
        ttk.Label(session, text="Plusieurs angles et éclairages aident à mieux reconnaître le visage.", style="CardMuted.TLabel", wraplength=310).pack(anchor="w", pady=(12, 0))
        ttk.Label(advanced, text="Affiner la reconnaissance", style="Section.TLabel", wraplength=300).pack(anchor="w", pady=(0, 18))
        for key, label, low, high in [("camera", "Index de la caméra", 0, 20), ("threshold", "Distance maximale", 1, 500), ("sharpness", "Netteté minimale", 0, 2000), ("confirmations", "Images de confirmation", 2, 30)]:
            ttk.Label(advanced, text=label, style="Card.TLabel").pack(anchor="w", pady=(0, 8))
            entry = ttk.Spinbox(advanced, textvariable=self.variables[key], from_=low, to=high, font=font(13), width=8)
            entry.pack(fill="x", pady=(0, 18))
            self.controls.append(entry)
        ttk.Label(advanced, text="Les réglages sont mémorisés au lancement de chaque opération.", style="CardMuted.TLabel", wraplength=300).pack(anchor="w", pady=8)
        ttk.Label(library, text="Ta bibliothèque", style="Section.TLabel").pack(anchor="w")
        ttk.Label(library, text="Sélectionne une personne pour compléter ses captures.", style="CardMuted.TLabel", wraplength=300).pack(anchor="w", pady=(8, 12))
        self.library_hint = tk.StringVar()
        ttk.Label(library, textvariable=self.library_hint, style="CardMuted.TLabel", wraplength=300).pack(anchor="w", pady=(0, 12))
        archive = ttk.Button(library, text="Archiver la sélection", command=self.archive, style="Quiet.TButton")
        archive.pack(side="bottom", fill="x", pady=(12, 0))
        self.controls.append(archive)
        edit = ttk.Button(library, text="Compléter ce profil", command=self.edit_selected)
        edit.pack(side="bottom", fill="x", pady=(12, 0))
        self.controls.append(edit)
        table = ttk.Frame(library, style="Card.TFrame")
        table.pack(fill="both", expand=True)
        self.people = ttk.Treeview(table, columns=("name", "count"), show="headings", height=4, selectmode="browse")
        self.people.heading("name", text="Personne")
        self.people.heading("count", text="Photos")
        self.people.column("name", width=185, minwidth=100)
        self.people.column("count", width=65, minwidth=60, anchor="center")
        self.people.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(table, orient="vertical", command=self.people.yview)
        scrollbar.pack(side="right", fill="y")
        self.people.configure(yscrollcommand=scrollbar.set)
        self.people.bind("<<TreeviewSelect>>", self.select_person)
        self.people.bind("<Double-1>", lambda event: self.edit_selected())
        footer = ttk.Frame(body)
        footer.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        ttk.Label(footer, text="FACE STUDIO  /  PREMIUM", style="Muted.TLabel", font=font(11, "bold")).pack(side="left")
        ttk.Label(footer, text="Échap pour arrêter · Données conservées en local", style="Muted.TLabel", font=font(11)).pack(side="right")
        root.bind("<Escape>", lambda event: self.stop())
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.refresh()
        if warning:
            self.append(warning + "\n")
        self.root.after(100, self.poll)

    def refresh(self):
        self.people.delete(*self.people.get_children())
        people = list_people(self.data)
        for name, count in people:
            self.people.insert("", "end", values=(name, count))
        self.library_hint.set("Aucun profil pour le moment." if not people else f"{len(people)} profil(s) enregistré(s)")
        model = "modèle disponible" if (self.data / "model.yml").is_file() else "entraînement nécessaire"
        self.summary.set(f"{len(people)} personne(s) · {sum(count for _, count in people)} captures · {model}")
        self.stat_people.set(f"{len(people):02d}")
        self.stat_captures.set(str(sum(count for _, count in people)))
        self.stat_model.set("Disponible" if (self.data / "model.yml").is_file() else "À entraîner")

    def select_person(self, _event):
        if self.process is None and self.people.selection():
            self.name.set(self.people.item(self.people.selection()[0], "values")[0])

    def toggle_activity(self):
        if self.activity_tabs.winfo_manager():
            self.activity_tabs.pack_forget()
            self.activity_toggle.configure(text="＋  Activité et résultats")
        else:
            self.activity_tabs.pack(fill="both", expand=True, pady=(8, 0))
            self.activity_toggle.configure(text="−  Masquer l’activité")

    def show_error_log(self):
        if not self.activity_tabs.winfo_manager():
            self.toggle_activity()
        self.activity_tabs.select(0)

    def edit_selected(self):
        if self.process is None and self.people.selection():
            self.select_person(None)
            self.sidebar.select(0)
            self.name_entry.focus_set()

    def append(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text)
        if int(self.log.index("end-1c").split(".")[0]) > 500:
            self.log.delete("1.0", "100.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def start(self, command):
        if self.process is not None:
            return
        try:
            settings = save_settings(self.data, {key: variable.get() for key, variable in self.variables.items()})
            if command == "entrainer":
                args = [sys.executable, "-u", str(ROOT / "main.py"), command, "--data", str(self.data)]
            else:
                name = person_name(self.name.get()) if command == "enregistrer" else ""
                if command == "reconnaitre" and not (self.data / "model.yml").is_file():
                    raise ValueError("Entraîne d'abord le modèle.")
                self.preview = tempfile.TemporaryDirectory(prefix="face-studio-")
                args = [sys.executable, "-u", str(ROOT / "worker.py"), command, "--data", str(self.data),
                        "--preview", self.preview.name, "--settings", json.dumps(settings), "--name", name]
            self.process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                            text=True, encoding="utf-8", errors="replace")
        except (ValueError, argparse.ArgumentTypeError, OSError) as error:
            if self.preview:
                self.preview.cleanup()
                self.preview = None
            messagebox.showerror("Impossible de démarrer", str(error), parent=self.root)
            return
        self.stopping = False
        self.last_seen.clear()
        self.progress.configure(maximum=settings["samples"], value=0)
        self.status.set(f"En cours : {command}…")
        self.append(f"\n→ {command}\n")
        for control in self.controls:
            control.configure(state="disabled")
        self.stop_button.configure(state="normal")
        threading.Thread(target=self.read_output, args=(self.process,), daemon=True).start()

    def read_output(self, process):
        with process.stdout:
            for line in process.stdout:
                self.events.put(("log", line))
        self.events.put(("done", process.wait()))

    def poll(self):
        updated = False
        for _ in range(100):
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                try:
                    event = json.loads(value)
                except (ValueError, TypeError):
                    self.append(value)
                    continue
                if not isinstance(event, dict):
                    self.append(value)
                    continue
                if "error" in event:
                    self.append("Erreur : " + event["error"] + "\n")
                    self.show_error_log()
                if "status" in event:
                    self.status.set(event["status"])
                    self.progress.configure(value=event.get("count", 0))
                    updated = True
                if "fps" in event:
                    distance = event.get("distance")
                    self.metrics.set(f"{event['fps']} FPS     ·     Netteté : {event['sharpness']}     ·     Distance : {round(distance, 1) if distance is not None else '—'}")
                name = event.get("name")
                if name and event.get("confirmed") and time.monotonic() - self.last_seen.get(name, -100) >= 30:
                    self.last_seen[name] = time.monotonic()
                    row = (datetime.now().isoformat(timespec="seconds"), name, round(event["distance"], 2))
                    self.history.append(row)
                    self.results.insert("", 0, values=row)
                    if len(self.history) > 1000:
                        self.history.pop(0)
                        self.results.delete(self.results.get_children()[-1])
            else:
                self.process = None
                if value != 0 and not self.stopping:
                    self.show_error_log()
                for control in self.controls:
                    control.configure(state="normal")
                self.stop_button.configure(state="disabled")
                self.status.set("Opération arrêtée." if self.stopping else
                                "Opération terminée. Réentraîne après un ajout de captures." if value == 0 else "Échec : consulte le journal.")
                if self.preview:
                    self.preview.cleanup()
                    self.preview = None
                self.video.clear()
                self.image = None
                self.refresh()
        if updated and self.preview:
            try:
                self.image = self.video.show_png(Path(self.preview.name) / "frame.png")
            except (tk.TclError, OSError):
                pass  # La première image peut ne pas être disponible immédiatement.
        if self.closing and self.process is None:
            self.root.destroy()
            return
        self.root.after(100, self.poll)

    def stop(self):
        process = self.process
        if process is not None and process.poll() is None:
            self.stopping = True
            self.status.set("Arrêt en cours…")
            if self.preview:
                try:
                    (Path(self.preview.name) / "stop").touch()
                except OSError:
                    process.terminate()
            else:
                process.terminate()
            self.root.after(2000, lambda: self.force_stop(process))

    def export(self):
        if not self.history:
            messagebox.showinfo("Session vide", "Aucune reconnaissance confirmée à exporter.", parent=self.root)
            return
        path = filedialog.asksaveasfilename(parent=self.root, defaultextension=".csv", initialfile="session.csv", filetypes=[("CSV", "*.csv")])
        if path:
            try:
                with open(path, "w", newline="", encoding="utf-8-sig") as stream:
                    writer = csv.writer(stream)
                    writer.writerow(["Date", "Personne", "Distance LBPH"])
                    # Évite d'interpréter les noms comme formules dans un tableur.
                    for date, name, distance in self.history:
                        writer.writerow([date, "'" + name if name.startswith(("=", "+", "-", "@")) else name, distance])
                self.append(f"Session exportée : {path}\n")
            except OSError as error:
                messagebox.showerror("Export impossible", str(error), parent=self.root)

    @staticmethod
    def force_stop(process):
        if process.poll() is None:
            process.kill()

    def archive(self):
        if self.process is not None or not self.people.selection():
            return
        name = self.people.item(self.people.selection()[0], "values")[0]
        if messagebox.askyesno("Archiver une personne", f"Retirer {name} de la reconnaissance ?\n"
                               "Ses captures seront archivées. Il faudra réentraîner le modèle.", parent=self.root):
            try:
                destination = archive_person(self.data, name)
            except (OSError, RuntimeError, argparse.ArgumentTypeError) as error:
                messagebox.showerror("Archivage impossible", str(error), parent=self.root)
            else:
                self.append(f"Captures et ancien modèle archivés : {destination}\n")
                self.status.set("Personne archivée. Réentraîne le modèle avant de reconnaître.")
            self.refresh()

    def close(self):
        self.closing = True
        self.stop()


def launch(data):
    try:
        root = tk.Tk()
    except tk.TclError as error:
        raise RuntimeError("Interface graphique indisponible : lance l'application dans une session graphique.") from error
    Dashboard(root, data)
    root.mainloop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=ROOT / "data")
    launch(parser.parse_args().data)
