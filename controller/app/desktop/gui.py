"""The SMP Host window: set up from an invite once, then Start hosting / Stop hosting with a button.

Like GitHub Desktop for the world: it shows the shared world (version, who uploaded it, who hosts now) and this PC, pulls
the world and hosts with one button, and uploads and hands over with another. The controller does the real work in a
background process; this window only starts it, watches it and asks it to stop. The look is Minecraft-style pixel art
drawn by pixel.py."""
import ctypes
import io
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
import tkinter.font
import webbrowser
from tkinter import filedialog, messagebox

from PIL import Image, ImageTk

from .. import errors
from .. import updates
from .. import identity
from .. import progress as progress_file
from ..version import VERSION
from .. import sessions as sessions_log
from ..rclone import Rclone
from ..shared import STALE_SECONDS, RcloneWorld, is_cap_error
from ..sharedtool import _ago as ago
from . import invite as invites
from . import feedback
from . import pixel
from . import restore as restoring
from . import textfit
from .home import APP_NAME, EULA_URL, KEEP, REMOTE, REMOVE, Home, find_tools
from .icon import make_icon
from .runner import Controller, Panel, last_error, log_size
from .shutdown import ShutdownGuard

TICK_MS = 1500
SHARED_EVERY = 600         # seconds between automatic looks at the shared world (each is a billable request on a free plan)
SHARED_EVERY_HOSTING = 1800  # ...while hosting, when this PC already knows what is going on
CAP_PAUSE = 1800           # ...and after the free daily allowance ran out: asking again only adds noise
IDENTITY_EVERY = 600       # seconds between checks for a newer server name/picture
W, H = 860, 680
PANEL_H = 192                # the two panels under the title: their height; everything below follows from it
SLOTS = 10


class Job:
    """A Windows job object around the controller, so 'cancel' also ends what it started (the playit agent, Java)."""

    def __init__(self):
        self.handle, self.k32 = None, None
        if sys.platform == "win32":
            try:
                k32 = ctypes.WinDLL("kernel32", use_last_error=True)
                k32.CreateJobObjectW.restype = ctypes.c_void_p
                k32.AssignProcessToJobObject.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
                k32.TerminateJobObject.argtypes = (ctypes.c_void_p, ctypes.c_uint)
                self.k32, self.handle = k32, k32.CreateJobObjectW(None, None)
            except Exception:
                self.handle = None

    def add(self, proc) -> None:
        if self.handle and proc is not None:
            self.k32.AssignProcessToJobObject(self.handle, int(proc._handle))

    def terminate(self) -> bool:
        return bool(self.handle) and bool(self.k32.TerminateJobObject(self.handle, 1))


# ---- pixel-art widgets on a canvas -----------------------------------------------------------------
class Scene:
    """A canvas with a pre-rendered backdrop. Keeps every PhotoImage alive (Tk forgets unreferenced ones)."""

    def __init__(self, parent, family, width=W, height=H):
        self.canvas = tk.Canvas(parent, width=width, height=height, highlightthickness=0, bd=0, bg="#2b3b4f")
        self.canvas.pack()
        self.family, self.photos, self.width = family, [], width
        self._fonts = {}

    def photo(self, img):
        ph = ImageTk.PhotoImage(img)
        self.photos.append(ph)
        return ph

    def image(self, img, x, y, anchor="nw", tags=()):
        return self.canvas.create_image(x, y, image=self.photo(img), anchor=anchor, tags=tags)

    def font(self, size, bold=False):
        return (self.family, size, "bold") if bold else (self.family, size)

    def text(self, x, y, text="", size=12, color="#ffffff", shadow=None, anchor="nw", width=0, bold=False, tags=()):
        return Text(self, x, y, text, size, color, shadow, anchor, width, bold, tags)

    def field(self, widget, x, y, w, h):
        self.canvas.create_window(x, y, window=widget, anchor="nw", width=w, height=h)
        return widget

    def measure(self, size, bold=False):
        """A function that returns the pixel width of a string in this font and size."""
        key = (size, bold)
        if key not in self._fonts:
            self._fonts[key] = tk.font.Font(font=self.font(size, bold))
        return self._fonts[key].measure

    def fit(self, text, size, width, max_lines=1, bold=False):
        """text wrapped into a box `width` pixels wide with at most max_lines lines (cut with '...'), so it can't spill out."""
        return textfit.fit_text(text, self.measure(size, bold), width, max_lines)

    def entry_style(self, size=11):
        return dict(bg="#000000", fg="#e0e0e0", insertbackground="#ffffff", relief="flat", highlightthickness=2,
                    highlightbackground="#a0a0a0", highlightcolor="#ffffff", selectbackground="#3e4fa8",
                    font=self.font(size))


class Text:
    """A canvas text with an optional Minecraft-style drop shadow."""

    def __init__(self, scene, x, y, text, size, color, shadow, anchor, width, bold, tags):
        c, font = scene.canvas, scene.font(size, bold)
        self.canvas, self.ids = c, []
        if shadow:
            self.ids.append(c.create_text(x + 2, y + 2, text=text, font=font, fill=shadow, anchor=anchor,
                                          width=width or 0, tags=tags))
        self.main = c.create_text(x, y, text=text, font=font, fill=color, anchor=anchor, width=width or 0, tags=tags)
        self.ids.append(self.main)

    def set(self, text, color=None):
        for i in self.ids:
            self.canvas.itemconfig(i, text=text)
        if color:
            self.canvas.itemconfig(self.main, fill=color)


class Button:
    """A stone button that lights up on hover, Minecraft style."""
    _count = 0

    def __init__(self, scene, x, y, w, h, label, command, size=12, primary=False):
        Button._count += 1
        self.tag = f"button{Button._count}"
        self.scene, self.command, self.enabled, self.hover = scene, command, True, False
        self.blocked, self.pressed = None, False          # blocked: what to say when a greyed-out button is clicked
        normal, hover = ("green", "green_hover") if primary else ("normal", "hover")
        self.photos = {s: scene.photo(pixel.button(w, h, state)) for s, state in
                       (("normal", normal), ("hover", hover), ("disabled", "disabled"))}
        c = scene.canvas
        self.bg = c.create_image(x, y, image=self.photos["normal"], anchor="nw", tags=(self.tag,))
        self.label = scene.text(x + w // 2, y + h // 2 - 1, label, size, "#ffffff", "#3f3f3f", anchor="center",
                                tags=(self.tag,))
        c.tag_bind(self.tag, "<Enter>", lambda _e: self._hover(True))
        c.tag_bind(self.tag, "<Leave>", lambda _e: self._hover(False))
        c.tag_bind(self.tag, "<ButtonPress-1>", self._press)
        c.tag_bind(self.tag, "<ButtonRelease-1>", self._release)

    def _press(self, _event):
        if not self.pressed and (self.enabled or self.blocked):
            self.pressed = True
            for item in self.label.ids:                   # the label dips like a pressed stone button
                self.scene.canvas.move(item, 0, 2)

    def _unpress(self):
        if self.pressed:
            self.pressed = False
            for item in self.label.ids:
                self.scene.canvas.move(item, 0, -2)

    def _release(self, _event):
        was_pressed = self.pressed
        self._unpress()
        if not was_pressed or not self.hover:             # let go somewhere else: nothing happens
            return
        if self.enabled:
            self.command()
        elif self.blocked:
            self.blocked()                                # a greyed-out button says why instead of doing nothing

    def set_label(self, text):
        self.label.set(text)

    def _hover(self, on):
        self.hover = on
        if not on:
            self._unpress()
        self.scene.canvas.config(cursor="hand2" if on and (self.enabled or self.blocked) else "")
        self._paint()

    def _paint(self):
        state = "disabled" if not self.enabled else ("hover" if self.hover else "normal")
        self.scene.canvas.itemconfig(self.bg, image=self.photos[state])
        self.scene.canvas.itemconfig(self.label.main, fill="#ffffff" if self.enabled else "#a0a0a0")

    def set_enabled(self, enabled):
        if enabled != self.enabled:
            self.enabled = enabled
            self._paint()


class Checkbox:
    def __init__(self, scene, x, y, label, size=11):
        self.scene, self.checked = scene, False
        c = scene.canvas
        tag = f"check{id(self)}"
        scene.image(pixel.slot(26), x, y, tags=(tag,))
        self.mark = c.create_image(x + 1, y + 1, image=scene.photo(pixel.check_mark(24)), anchor="nw",
                                   state="hidden", tags=(tag,))
        scene.text(x + 36, y + 4, label, size, pixel.LABEL, tags=(tag,))
        c.tag_bind(tag, "<Button-1>", lambda _e: self.toggle())

    def toggle(self):
        self.checked = not self.checked
        self.scene.canvas.itemconfig(self.mark, state="normal" if self.checked else "hidden")


_BACKGROUND = {}


def backdrop(title_text, icon_path=None):
    """The landscape with the server's chunky name and its picture; returns (image, y just below the title)."""
    if "img" not in _BACKGROUND:
        _BACKGROUND["img"] = pixel.background(W, H)
    img = _BACKGROUND["img"].copy()
    t = pixel.fit_title(title_text, 540)
    img.alpha_composite(t, ((W - t.width) // 2, 12))
    frame = pixel.panel(84, 84)                                  # the server's picture, in an inventory-style frame
    img.alpha_composite(frame, (22, 14))
    picture = None
    if icon_path:
        try:
            picture = Image.open(icon_path).convert("RGBA").resize((64, 64), Image.NEAREST)
        except OSError:
            picture = None
    img.alpha_composite(picture or pixel.grass_block(64), (32, 24))
    return img, 12 + t.height


# ---- the app ---------------------------------------------------------------------------------------
class App:
    def __init__(self, root: tk.Tk, home: Home, family: str):
        self.root, self.home, self.family = root, home, family
        self.tools = find_tools()
        self.controller = Controller(home, self.tools)
        self.job = Job()
        self.events = queue.Queue()
        self.shared_state, self.shared_error, self.shared_at = None, "", 0.0
        self.local, self.listening, self.polling = None, False, False
        self.busy, self.expect_exit, self.log_start, self.log_pos = "", False, 0, 0
        self.quitting, self.log_started = False, False
        self.tray, self.scene, self.avatars = None, None, {}
        self.tick_job, self.ident_stamp, self.identity_at = None, None, 0.0
        self.progress_job, self.refreshing, self.takeover = None, False, False
        self.py, self._waiting_since, self._logged_error = 0, None, ""
        self.main_ready = False                          # True while the main screen (not setup) is the one showing
        self.log_hint_on = False                         # the grey 'nothing here yet' line is in the log box
        self.update_info, self.update_btn = None, None   # a newer release, when there is one (see updates.py)
        self.progress_path = os.path.join(home.state_dir, "progress.json")
        root.title(APP_NAME)
        root.geometry(f"{W}x{H}")
        root.resizable(False, False)
        root.configure(bg="#2b3b4f")
        icon = ImageTk.PhotoImage(pixel.grass_block(64))
        root.iconphoto(True, icon)
        self._icon = icon
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(4000, self.check_updates)
        self.guard = ShutdownGuard(root, self.hosting, lambda: self.later(self.shutdown_attempt))
        self.show_main() if home.configured() else self.show_setup()
        root.after(100, self.pump)

    # -- plumbing ----------------------------------------------------------------------------------
    def later(self, fn):
        self.events.put(fn)

    def in_background(self, work, done=None):
        def target():
            try:
                result, error = work(), None
            except Exception as exc:          # shown to the user by `done`
                result, error = None, exc
            if done:
                self.later(lambda: done(result, error))
        threading.Thread(target=target, daemon=True).start()

    def pump(self):
        while True:
            try:
                fn = self.events.get_nowait()
            except queue.Empty:
                break
            fn()
        self.root.after(100, self.pump)

    def new_scene(self):
        self.main_ready = False                          # the old screen's widgets are about to be destroyed
        if self.scene is not None:
            self.scene.canvas.destroy()
        self.scene = Scene(self.root, self.family)
        return self.scene

    def world(self) -> RcloneWorld:
        settings = self.home.settings()
        os.environ["RCLONE_CONFIG"] = self.home.rclone_conf
        return RcloneWorld(settings.get("SHARED_REMOTE", ""), self.home.server_dir, settings.get("HOST_NAME", ""),
                           Rclone(self.tools.rclone or "rclone"))

    def copy(self, text):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)

    # -- first start: the invite --------------------------------------------------------------------
    def history_dialog(self):
        """Who hosted and when, the backups on this PC (matched to those sessions), and putting a backup back."""
        win = tk.Toplevel(self.root)
        win.title("Hosting history and backups")
        win.transient(self.root)
        win.resizable(False, False)
        dw, dh = 760, 616
        s = Scene(win, self.family, dw, dh)
        s.image(pixel.panel(dw, dh), 0, 0)
        inner = dw - 48
        s.text(24, 18, "Who hosted, and backups", 14, "#202020", bold=True)
        s.text(24, 50, "Hosting history, newest first: who, from when to when, how long.", 10, pixel.LABEL)
        box = {k: v for k, v in s.entry_style(10).items() if k != "insertbackground"}      # (a list has no text cursor)
        who = s.field(tk.Listbox(s.canvas, activestyle="none", exportselection=False, **box), 24, 76, inner, 168)
        who.insert("end", "Loading...")
        s.text(24, 252, "!  means no stop was recorded: the PC crashed, lost power or was closed.", 9, pixel.LABEL)
        s.text(24, 286, "Backups on this PC", 10, pixel.LABEL)
        s.text(24, 304, "Made when the server stops, and before this PC's world is replaced.", 9, "#505050")
        pick = s.field(tk.Listbox(s.canvas, activestyle="none", exportselection=False, **box), 24, 328, inner, 150)
        note = s.text(24, 490, s.fit("Pick the backup made AFTER the session you want back: a backup holds the world as "
                                     "it was when it was made.", 10, inner, 2), 10, pixel.LABEL)
        data = {"sessions": [], "backups": restoring.list_backups(self.home.backup_dir)}
        chars = lambda text, n: textfit.one_line(str(text), len, n)

        def backup_row(b):
            during = sessions_log.session_at(data["sessions"], b["time"])
            row = f"{sessions_log._day(b['time'])}, {sessions_log._clock(b['time']):>8}   {b['size'] / 1048576:>4.0f} MB"
            if during:
                host = chars(during.get("host", "?"), 18)
                if b["time"] - float(during["start"]) < 300:
                    row += f"   as {host}'s session started (the world BEFORE it)"
                elif during.get("end") is not None and float(during["end"]) - b["time"] < 300:
                    row += f"   as {host}'s session ended (includes it)"
                else:
                    row += f"   during {host}'s session"
            return row

        def show_backups():
            pick.delete(0, "end")
            for b in data["backups"]:
                pick.insert("end", backup_row(b))
            if not data["backups"]:
                pick.insert("end", "No backups on this PC yet.")

        def loaded(result, error):
            who.delete(0, "end")
            if error:
                who.insert("end", errors.summarize(error)[0] + ". " + errors.summarize(error)[1])
                return
            data["sessions"] = list(reversed(result))
            now = time.time()
            for entry in result:
                who.insert("end", sessions_log.row(entry, now))
            if not result:
                who.insert("end", "Nothing recorded yet. From now on every session is written here.")
            show_backups()
        show_backups()
        self.in_background(lambda: self.world().sessions(), loaded)

        def restore_selected():
            chosen = pick.curselection()
            if not chosen or not data["backups"]:
                return note.set("Click a backup in the second list first.", "#aa0000")
            win.destroy()
            self.restore_zip(data["backups"][chosen[0]]["path"])

        def restore_other():
            path = filedialog.askopenfilename(parent=win, title="Pick a world backup (zip)",
                                              filetypes=[("World backup (zip)", "*.zip")])
            if path:
                win.destroy()
                self.restore_zip(path)
        Button(s, 24, 556, 210, 40, "Restore selected", restore_selected, size=10, primary=True)
        Button(s, 246, 556, 190, 40, "Choose a file...", restore_other, size=10)
        Button(s, dw - 24 - 120, 556, 120, 40, "Close", win.destroy, size=10)

    def restore_zip(self, path):
        """Put a backup (a world-....zip) back as THE world: for a session that was never uploaded."""
        if self.hosting():
            return messagebox.showinfo(APP_NAME, "Stop hosting first, then restore a backup.")
        if not messagebox.askyesno(APP_NAME, f"Use this backup as the world?\n\n{os.path.basename(path)}\n\nThis PC's "
                                             "current world is kept in a folder next to it, nothing is deleted. Then "
                                             "press Start hosting and Stop hosting: that uploads the backup as the "
                                             "shared world for everyone (what the shared world had is kept in the "
                                             "history for a few days).", icon="warning"):
            return
        self.busy = "Restoring the backup..."

        def done(_result, error):
            self.busy = ""
            if error:
                return messagebox.showerror(APP_NAME, str(error))
            messagebox.showinfo(APP_NAME, "Restored. Now press Start hosting (it keeps this world), then Stop hosting "
                                          "and wait for Done: that uploads it for everyone.")
            self.refresh_shared(full=True)
        self.in_background(lambda: restoring.restore(self.home, Rclone(self.tools.rclone or "rclone"), path), done)

    def new_invite(self):
        """Set up again from a new invite (the server moved to other storage, or a key was replaced)."""
        if self.hosting():
            return messagebox.showinfo(APP_NAME, "Stop hosting first, then use a new invite.")
        self.show_setup(can_cancel=True)

    def show_setup(self, can_cancel=False):
        s = self.new_scene()
        img, top = backdrop(APP_NAME.upper())
        pw, ph = W - 120, 452
        px, py = 60, top + 28
        img.alpha_composite(pixel.panel(pw, ph), (px, py))
        s.image(img, 0, 0)
        x, inner = px + 28, pw - 56
        s.text(x, py + 22, "1. Paste the invite code", 11, pixel.LABEL)
        Button(s, px + pw - 28 - 120, py + 14, 120, 28, "Paste", self.paste_invite, size=10)
        s.text(x, py + 50, s.fit(f"An invite code is one long line of text that starts with {invites.PREFIX} It holds this "
                                 "server's settings. Whoever runs the server makes it for you and sends it privately.",
                                 9, inner, 2), 9, "#505050")
        self.invite_box = s.field(tk.Text(s.canvas, wrap="char", padx=8, pady=6, **s.entry_style(10)),
                                  x, py + 84, inner, 84)
        s.text(x, py + 196, "2. A name for this PC (others see it while you host)", 11, pixel.LABEL)
        self.name_var = tk.StringVar(value=self.home.settings().get("HOST_NAME") or os.environ.get("USERNAME", ""))
        s.field(tk.Entry(s.canvas, textvariable=self.name_var, **s.entry_style(12)), x, py + 222, 300, 32)
        self.eula = Checkbox(s, x, py + 280, "3. I accept the Minecraft EULA")
        link_x = x + 36 + tk.font.Font(font=s.font(11)).measure("3. I accept the Minecraft EULA") + 12
        s.text(link_x, py + 284, "(read it)", 11, "#2c55c9", tags=("eula_link",))
        s.canvas.tag_bind("eula_link", "<Button-1>", lambda _e: webbrowser.open(EULA_URL))
        self.setup_btn = Button(s, x, py + 336, 200, 40, "Set up", self.do_setup, primary=True)
        if can_cancel:
            Button(s, x + 216, py + 336, 140, 40, "Cancel", self.show_main)
        self.setup_msg = s.text(x, py + 392, "", 11, pixel.LABEL, width=inner)
        self.root.after(300, lambda: self.paste_invite(quiet=True))      # an invite already on the clipboard is used

    def setup_note(self, text, error=False):
        self.setup_msg.set(text, "#aa0000" if error else pixel.LABEL)

    def paste_invite(self, quiet=False):
        """Fill the invite box from the clipboard. quiet: only when it holds an invite and the box is still empty."""
        try:
            text = self.root.clipboard_get()
        except tk.TclError:
            text = ""
        text = "".join(text.split())                      # a code copied from a chat may be wrapped over several lines
        if quiet and (self.invite_box.get("1.0", "end").strip() or not text.startswith(invites.PREFIX)):
            return
        if not text.startswith(invites.PREFIX):
            return self.setup_note("The clipboard doesn't hold an invite code. Copy the whole code first (it starts "
                                   f"with {invites.PREFIX}), then click Paste.", error=True)
        self.invite_box.delete("1.0", "end")
        self.invite_box.insert("1.0", text)
        self.setup_note("Found an invite and pasted it. Tick the Minecraft EULA below, then click Set up.")

    def do_setup(self):
        if not self.invite_box.get("1.0", "end").strip():
            return self.setup_note("Paste the invite code first: copy it, then click Paste.", error=True)
        try:
            data = invites.read(self.invite_box.get("1.0", "end"))
        except invites.InviteError as exc:
            return self.setup_note(str(exc), error=True)
        name = self.name_var.get().strip()
        if not name:
            return self.setup_note("Pick a name for this PC.", error=True)
        if not self.eula.checked:
            return self.setup_note("Minecraft needs you to accept its EULA (tick box 3).", error=True)
        missing = self.tools.missing()
        if missing:
            return self.setup_note(f"This install is missing {', '.join(missing)}. Reinstall {APP_NAME}.", error=True)
        self.setup_btn.set_enabled(False)
        self.setup_note("Setting up...")

        def work():
            env = self.home.apply_invite(data, name)
            self.home.install_server(env.get("MC_VERSION") or "26.3", True,
                                     report=lambda msg: self.later(lambda: self.setup_note(msg)))
            self.later(lambda: self.setup_note("Checking the shared world..."))
            self.world().read_state()             # proves the storage key works
            return env

        def done(_env, error):
            if error:
                self.setup_btn.set_enabled(True)
                return self.setup_note(f"Setup failed: {error}", error=True)
            self.show_main()
        self.in_background(work, done)

    # -- the main window ------------------------------------------------------------------------------
    def show_main(self):
        settings = self.home.settings()
        face = self.home.identity()
        self.ident_stamp = self.home.identity_stamp()
        self.panel = Panel(self.home.panel_port(), settings.get("PANEL_PASSWORD", ""))
        s = self.new_scene()
        img, top = backdrop(face["name"], face["icon_path"])
        address = settings.get("PUBLIC_ADDRESS", "")
        py = self.py = top + 32 + (22 if face["description"] else 0) + (34 if address else 0)
        by = py + PANEL_H + 12                                   # the row of buttons, 12 under the panels
        log_y = by + 40 + 12                                     # the log panel, 12 under the buttons
        img.alpha_composite(pixel.wood_panel(400, PANEL_H), (20, py))
        img.alpha_composite(pixel.plaque(190, 26), (20 + 105, py - 12))
        img.alpha_composite(pixel.panel(400, PANEL_H), (440, py))
        img.alpha_composite(pixel.panel(W - 40, H - log_y - 14), (20, log_y))
        s.image(img, 0, 0)

        Button(s, W - 20 - 130, 22, 130, 28, "Hide to tray", self.hide_to_tray, size=10)
        Button(s, W - 20 - 130, 56, 130, 28, "New invite", self.new_invite, size=10)   # under it: the picture is top left
        Button(s, W - 20 - 130, 90, 130, 28, "History", self.history_dialog, size=10)
        self.update_btn = None
        if self.update_info:
            self.add_update_button(s)

        y = top + 6                                      # under the title: description, then the address to share
        if face["description"]:
            s.text(W // 2, y, s.fit(face["description"], 11, 540), 11, "#ffffff", "#2a2a2a", anchor="n")
            y += 24
        if address:                                      # the address and its Copy button, centred as one group
            text_w = tk.font.Font(font=s.font(12)).measure(address)
            left = (W - (text_w + 14 + 76)) // 2
            s.text(left, y + 14, address, 12, "#ffffff", "#2a2a2a", anchor="w")
            self.copy_btn = Button(s, left + text_w + 14, y, 76, 26, "Copy", lambda: self.copy_address(address), size=10)

        # -- the shared world (left panel): what is stored and who hosts, then a light and Refresh along the bottom --------
        s.text(20 + 200, py + 1, "SHARED WORLD", 10, "#f0dcbe", "#3b2410", anchor="center")
        self.shared_txt = s.text(40, py + 30, "Looking...", 12, "#ffffff", "#3b2410")
        self.light = s.canvas.create_rectangle(40, py + 148, 52, py + 160, fill="#7f7f7f", outline="#000000", width=2)
        self.light_txt = s.text(62, py + 145, "", 10, "#ffffff", "#3b2410")
        self.checked_txt = s.text(40, py + 168, "", 9, "#f0dcbe", "#3b2410")
        self.refresh_btn = Button(s, 20 + 400 - 108, py + 146, 92, 28, "Refresh",
                                  lambda: self.refresh_shared(full=True, manual=True), size=10)

        # -- this PC (right panel): its name and a status chip, one or two lines, then players or the progress bar --------
        s.text(460, py + 16, s.fit(f"This PC ({settings.get('HOST_NAME', '')})", 11, 220), 11, pixel.LABEL)
        self.chip_dot = s.canvas.create_rectangle(0, 0, 0, 0, fill="#7f7f7f", outline="#000000", width=2)
        self.chip_txt = s.text(820, py + 16, "", 10, "#404040", anchor="ne")
        self.local_txt = s.text(460, py + 46, "", 12, "#202020")
        self.players_txt = s.text(460, py + 106, "", 10, pixel.LABEL)
        self.slot_imgs, self.slot_bgs = [], []
        for i in range(SLOTS):
            self.slot_bgs.append(s.image(pixel.slot(36), 460 + i * 36, py + 128))
            self.slot_imgs.append(s.canvas.create_image(460 + i * 36 + 4, py + 132, anchor="nw", state="hidden"))

        gap = 8
        bw = (W - 40 - 5 * gap) // 6
        labels = (("Start hosting", self.start_hosting, True), ("Stop hosting", self.stop_hosting, False),
                  ("Web panel", self.open_panel, False), ("Server info", self.server_dialog, False),
                  ("Invite friend", self.invite_dialog, False),
                  ("Open folder", self.open_folder, False))
        buttons = [Button(s, 20 + i * (bw + gap), by, bw, 40, label, command, size=10, primary=primary)
                   for i, (label, command, primary) in enumerate(labels)]
        self.host_btn, self.stop_btn, self.panel_btn = buttons[0], buttons[1], buttons[2]
        self.host_btn.blocked = lambda: self.say(self.why("start"), "warn")      # a greyed-out button explains itself
        self.stop_btn.blocked = lambda: self.say(self.why("stop"), "warn")
        self.panel_btn.blocked = lambda: self.say(self.why("panel"), "warn")

        # -- the log: a heading row (with Copy log), then the text --------------------------------------------------------
        s.text(40, log_y + 14, "SERVER LOG", 9, pixel.LABEL)
        Button(s, W - 20 - 16 - 110, log_y + 8, 110, 24, "Copy log", self.copy_log, size=9)
        self.log_box = s.field(tk.Text(s.canvas, wrap="word", padx=8, pady=6, state="disabled", **s.entry_style(10)),
                               36, log_y + 38, W - 72, H - log_y - 68)
        size = log_size(self.home.log_path)
        self.log_pos, self.log_started = max(0, size - 6000), False
        self.log_box.tag_config("hint", foreground="#7a7a7a")
        self.log_box.config(state="normal")
        self.log_box.insert("end", "Nothing here yet. This fills in when you start hosting.", "hint")
        self.log_box.config(state="disabled")
        self.log_hint_on = True
        s.text(W - 24, H - 7, f"SMP Host {VERSION}", 8, "#f0f0f0", "#2a2a2a", anchor="e")     # which build this is, for support
        self.bar = feedback.ProgressBar(s, 460, py + 138, 360)     # the progress bar for starting / saving / downloading
        self.toast = feedback.Toast(s, W // 2, 68, w=460, h=44)    # the short message after a click (over the title area:
        #                                                            a canvas item can't sit on top of the log's text box)
        self.main_ready = True
        self.refresh_shared(full=True)
        self.tick()
        if self.progress_job is not None:
            self.root.after_cancel(self.progress_job)
        self.progress_job = self.root.after(250, self.progress_tick)

    # -- a newer version ----------------------------------------------------------------------------------------
    def check_updates(self):
        """Ask GitHub (in the background) whether there is a newer release; ask again in six hours."""
        if self.home.settings().get("CHECK_UPDATES", "1") != "0":
            self.in_background(lambda: updates.check(VERSION), self._update_found)
        self.root.after(6 * 3600 * 1000, self.check_updates)

    def _update_found(self, result, _error):
        self.update_info = result
        if result and self.main_ready and self.update_btn is None:
            self.add_update_button(self.scene)
            self.say(f"A newer SMP Host ({result['version']}) is out. Use the green Update button.", "ok")

    def add_update_button(self, s):
        info = self.update_info
        self.update_btn = Button(s, W - 20 - 130, 124, 130, 28, f"Update {info['version']}", self.open_update, size=10,
                                 primary=True)

    def open_update(self):
        webbrowser.open((self.update_info or {}).get("url") or updates.PAGE)
        self.say("Opening the download page. Close SMP Host (also in the tray) before you install it.", "info")

    # -- feedback: the bar, the toast, and what a click says back -----------------------------------------
    def say(self, text, kind="info"):
        if self.scene is not None and self.main_ready:
            self.toast.show(text, kind)

    def why(self, button):
        return feedback.blocked_reason(button, self.hosting(), self.listening, self.busy, self.controller.running())

    def progress_tick(self):
        """Four times a second: what is the controller doing right now, and how far along is it?"""
        self.progress_job = None
        try:
            if self.scene is None or not self.main_ready:
                return
            job = progress_file.read(self.progress_path)
            getting_ready = self.controller.running() and not self.listening
            if job:
                label, fraction, detail, span, since = feedback.bar_text(job)
                self.bar.show(self.scene.fit(label, 10, 300), fraction, detail, span, since)
                self._waiting_since = None
            elif self.bar.visible:                              # the controller's job file is gone
                self._waiting_since = None
                if self.bar.target >= 0.8:
                    self.bar.complete()                         # it got far, so it worked: glide to 100, then away
                elif getting_ready or self.busy:
                    pass                                        # still starting: leave the bar where it is (never back)
                else:
                    self.bar.hide()                             # it stopped early (an error): no pretend 100
            elif getting_ready or self.busy:                    # the controller hasn't said anything yet: creep forward
                self._waiting_since = self._waiting_since or time.time()
                self.bar.show(self.scene.fit(self.busy_head(), 10, 300), None, "", (0.0, 0.06), self._waiting_since)
            shown = "hidden" if self.bar.visible else "normal"
            for item in self.slot_bgs:
                self.scene.canvas.itemconfig(item, state=shown)
            if self.bar.visible:
                self.players_txt.set("")
        except tk.TclError:
            return                                       # the window is being rebuilt or closed
        active = bool(job) or getting_ready or bool(self.busy) or self.bar.visible
        self.progress_job = self.root.after(250 if active else 1000, self.progress_tick)    # slow when nothing is going on

    def busy_head(self):
        """The first words of the busy message, for the bar's caption."""
        return (self.busy or "Starting up...").split("...")[0].strip() + "..."

    def busy_sentence(self):
        """The busy message as one or two short lines for the This PC panel."""
        if "handing over" in self.busy:
            return "Saving and handing over. Keep the PC on until it says Done."
        return self.busy

    def copy_address(self, address):
        self.copy(address)
        self.copy_btn.set_label("Copied!")
        self.root.after(1500, self._copy_label_back)
        self.say(f"Copied {address}. In Minecraft: Multiplayer > Add Server > paste it.", "ok")

    def _copy_label_back(self):
        if self.main_ready:
            try:
                self.copy_btn.set_label("Copy")
            except tk.TclError:
                pass                                       # the screen was rebuilt meanwhile

    def open_folder(self):
        os.startfile(self.home.root)
        self.say("Opened the SMP Host folder (backups, the server, the log).", "info")

    def avatar(self, name):
        if name not in self.avatars:
            self.avatars[name] = self.scene.photo(pixel.avatar(name, 28))
        return self.avatars[name]

    # -- watching -----------------------------------------------------------------------------------------
    def hosting(self) -> bool:
        return self.controller.running() or self.listening

    def refresh_shared(self, full=False, manual=False):
        """Look at the shared world. Every look is a billable request on a free cloud plan, so automatic looks are rare;
        the Refresh button (manual) also checks for a newer server name/picture, and says what it found."""
        if manual:
            if self.refreshing:
                return
            self.refreshing = True
            self.refresh_btn.set_label("Checking...")
            self.refresh_btn.set_enabled(False)
            self.say("Checking the shared world...", "info")
        before = self.shared_state
        self.shared_at = time.time()
        check_face = (full or self.shared_at - self.identity_at > IDENTITY_EVERY) and not self.hosting()
        if check_face:
            self.identity_at = self.shared_at

        def work():
            world = self.world()
            state = world.read_state()
            if check_face:                               # while hosting, the controller keeps the server's face in step
                try:
                    self.home.sync_identity(world)       # a newer name/picture from a friend, or publish ours
                except Exception:
                    pass                                 # not important enough to complain about; it retries
            return state

        def done(state, error):
            self.shared_state, self.shared_error = state, (str(error) if error else "")
            if error and is_cap_error(error):
                self.shared_at = time.time() + CAP_PAUSE - SHARED_EVERY     # wait longer before the next automatic look
            if self.scene is not None and self.main_ready:
                try:
                    self.checked_txt.set("Checked " + time.strftime("%I:%M %p").lstrip("0"))
                except tk.TclError:
                    pass
            if manual:
                self.refreshing = False
                try:
                    self.refresh_btn.set_label("Refresh")
                    self.refresh_btn.set_enabled(True)
                    kind, text = feedback.describe_refresh(before, state, error, self.home.settings().get("HOST_NAME", ""))
                    self.say(text, kind)
                except tk.TclError:
                    pass                                 # the window was rebuilt while it looked
            self.render()
        self.in_background(work, done)

    def poll_controller(self):
        """Is the controller up, and what does it say? Connecting to a port nobody listens on takes about a second on
        Windows, so this must never run on the window's own thread (it used to, and froze the window every tick)."""
        if self.polling:
            return
        self.polling = True

        def work():
            return (True, self.panel.status()) if self.panel.listening() else (False, None)

        def done(result, _error):
            self.polling = False
            self.listening, self.local = result if result else (False, None)
            self.render()
        self.in_background(work, done)

    def tick(self):
        if self.tick_job is not None:                    # show_main() can run again (a rename): one timer only
            self.root.after_cancel(self.tick_job)
            self.tick_job = None
        if self.home.identity_stamp() != self.ident_stamp:
            return self.show_main()                      # the name, description or picture changed: redraw the header
        self.poll_controller()                           # in the background: it can take a second on Windows
        self.guard.sync()                                # Windows warns before shutting down while we host
        if self.controller.proc is not None and not self.controller.running():
            self.controller_ended()
        if self.expect_exit and not self.hosting():
            self.expect_exit, self.busy = False, ""
            self.refresh_shared()
            if not self.quitting:
                messagebox.showinfo(APP_NAME, "Done: the world is uploaded and someone else can host now.")
        looking = self.root.state() not in ("withdrawn", "iconic")       # nobody is watching a hidden or minimized window
        if looking and time.time() - self.shared_at > (SHARED_EVERY_HOSTING if self.hosting() else SHARED_EVERY):
            self.refresh_shared()
        self.read_log()
        self.render()
        self.tick_job = self.root.after(TICK_MS, self.tick)

    def controller_ended(self):
        code = self.controller.exit_code()
        self.controller.proc = None
        if self.expect_exit:
            return
        self.busy = ""
        why = last_error(self.home.log_path, self.log_start)
        if code not in (0, None) or why:
            plain = errors.explain(why)
            if plain:
                text = plain + "\n\n(" + why[:240] + ")"
            else:
                text = why or f"Hosting stopped unexpectedly (code {code}). See the log below."
            messagebox.showwarning(APP_NAME, text)
        self.refresh_shared()

    def read_log(self):
        try:
            with open(self.home.log_path, "rb") as f:
                if self.log_pos > f.seek(0, 2):           # the log was rotated (it starts over at 1 MB)
                    self.log_pos = 0
                f.seek(self.log_pos)
                chunk = f.read()
        except OSError:
            return
        end = chunk.rfind(b"\n")                          # only whole lines; the rest is still being written
        if end < 0:
            return
        if self.log_pos and not self.log_started:         # started in the middle of the file: skip the partial line
            chunk = chunk[chunk.find(b"\n") + 1:end + 1]
        else:
            chunk = chunk[:end + 1]
        self.log_started = True
        self.log_pos += end + 1
        text = friendly_log(chunk.decode("utf-8", "replace"))
        if text:
            self._clear_hint()
            self.log_box.config(state="normal")
            self.log_box.insert("end", text)
            self.log_box.delete("1.0", "end-400l")          # keep the last 400 lines
            self.log_box.see("end")
            self.log_box.config(state="disabled")

    def shared_view(self, me, now):
        """(lines, light colour, light text) for the Shared World box. Each line is (text, most lines it may use)."""
        s = self.shared_state
        if self.shared_error:
            head, detail = errors.summarize(self.shared_error)
            return [(head, 1), (detail, 2), ("More in the log below.", 1)], "#d23b3b", "Offline"
        if s is None:
            if not self.shared_at:
                return [("Looking...", 1)], "#7f7f7f", ""
            return [("The shared world is empty.", 1), ("The owner uploads it first.", 1)], "#7f7f7f", ""
        when = ago(now - float(s["lastPush"])) if s.get("lastPush") else "never"
        lines = [(f"Version {s.get('version', 0)}, uploaded {when}", 1),
                 (f"Last host: {s.get('lastHost') or s.get('host') or 'nobody'}", 1)]
        if s.get("status") == "hosting":
            alive = now - float(s.get("lastSeen", 0)) < STALE_SECONDS
            who = "you" if s.get("host") == me else s.get("host")
            lines.append((f"Hosting now: {who}", 1))
            light, light_text = ("#4cc04c", "You are hosting") if who == "you" else \
                (("#e6b422", f"{who} is hosting") if alive else ("#d23b3b", f"{who} isn't responding"))
        else:
            lines.append(("Hosting now: nobody", 1))
            light, light_text = "#4cc04c", "Free: you can start"
        if s.get("pushing"):
            if s.get("pushingBy") == me and self.hosting():
                lines.append(("Uploading the world right now...", 1))
            else:
                lines.append((f"{s.get('pushingBy') or 'someone'}'s last upload never finished!", 1))
        return lines, light, light_text

    def local_view(self, me, now):
        """(chip colour, chip text, one or two lines, player names) for the This PC panel."""
        if self.busy:
            return "#e6b422", "Working", self.busy_sentence(), []
        if self.local:
            state, players = self.local.get("state"), self.local.get("players", 0)
            names = list(self.local.get("names") or [])
            body, chip, color = {
                "asleep": ("The server is asleep and wakes up when someone joins.", "Hosting", "#4cc04c"),
                "starting": ("The server is starting. This takes about a minute.", "Starting", "#e6b422"),
                "online": (f"Online with {players} player{'s' if players != 1 else ''}.", "Online", "#4cc04c"),
                "stopping": ("The server is saving and stopping...", "Saving", "#e6b422"),
            }.get(state, (f"Hosting ({state}).", "Hosting", "#4cc04c"))
            return color, chip, body, names
        if self.controller.running():
            return "#e6b422", "Getting ready", "Getting the world ready. This can take a minute.", []
        s = self.shared_state or {}
        if s.get("status") == "hosting" and s.get("host") != me and now - float(s.get("lastSeen", 0)) < STALE_SECONDS:
            return "#7f7f7f", "Not hosting", f"{s.get('host')} is hosting. Join their server.", []
        return "#7f7f7f", "Not hosting", "Press Start hosting when it's your turn.", []

    def set_chip(self, color, text):
        """The small coloured square and word at the top right of the This PC panel."""
        sc = self.scene
        self.chip_txt.set(text)
        width = sc.measure(10)(text)
        top = self.py + 17
        sc.canvas.coords(self.chip_dot, 820 - width - 22, top, 820 - width - 10, top + 12)
        sc.canvas.itemconfig(self.chip_dot, fill=color)

    def render(self):
        if self.scene is None or not self.main_ready:
            return
        sc = self.scene
        me = self.home.settings().get("HOST_NAME", "")
        now = time.time()
        lines, light, light_text = self.shared_view(me, now)
        self.shared_txt.set("\n".join(sc.fit(text, 12, 360, n) for text, n in lines))
        sc.canvas.itemconfig(self.light, fill=light)
        self.light_txt.set(sc.fit(light_text, 10, 230))
        if self.shared_error and self.shared_error != self._logged_error:
            self._logged_error = self.shared_error               # the full text of an error goes to the log, once
            self.log_add("!  " + self.shared_error)
        elif not self.shared_error:
            self._logged_error = ""

        color, chip, body, names = self.local_view(me, now)
        self.set_chip(color, chip)
        self.local_txt.set(sc.fit(body, 12, 360, 2))
        self.players_txt.set(f"Players online: {len(names)}/{SLOTS}" if self.local and not self.bar.visible else "")
        for i, item in enumerate(self.slot_imgs):
            if i < len(names):
                sc.canvas.itemconfig(item, image=self.avatar(names[i]), state="normal")
            else:
                sc.canvas.itemconfig(item, state="hidden")
        hosting = self.hosting()
        self.host_btn.set_enabled(not hosting and not self.busy)
        self.stop_btn.set_enabled(hosting and not self.expect_exit and not self.busy)
        self.panel_btn.set_enabled(self.listening)

    def _clear_hint(self):
        if self.log_hint_on:
            self.log_hint_on = False
            self.log_box.config(state="normal")
            self.log_box.delete("1.0", "end")
            self.log_box.config(state="disabled")

    def copy_log(self):
        text = "" if self.log_hint_on else self.log_box.get("1.0", "end").strip()
        if not text:
            return self.say("The log is empty.", "warn")
        self.copy(text)
        self.say("Copied the log. Paste it where you ask for help.", "ok")

    def log_add(self, text):
        """A line for the log box that isn't from the controller's own log (the full error behind a short message)."""
        try:
            self._clear_hint()
            self.log_box.config(state="normal")
            self.log_box.insert("end", time.strftime("%H:%M:%S") + "  " + text + "\n")
            self.log_box.see("end")
            self.log_box.config(state="disabled")
        except tk.TclError:
            pass

    # -- actions --------------------------------------------------------------------------------------
    def start_hosting(self):
        s = self.shared_state or {}
        me = self.home.settings().get("HOST_NAME", "")
        if s.get("status") == "hosting" and s.get("host") != me \
                and time.time() - float(s.get("lastSeen", 0)) < STALE_SECONDS:
            return messagebox.showinfo(APP_NAME, f"{s.get('host')} is hosting right now. Join their server, or ask "
                                                 "them to stop hosting first.")
        self.takeover = False
        if s.get("status") == "hosting" and s.get("host") != me:      # not alive (checked above): its PC may be gone
            minutes = int((time.time() - float(s.get("lastSeen", 0))) // 60)
            saved = (f"The newest saved copy of the world is from {ago(time.time() - float(s['lastPush']))} (the app saves "
                     "a checkpoint every 20 minutes while hosting)." if s.get("lastPush") else
                     "Nothing has been saved to the cloud yet.")
            if not messagebox.askyesno(APP_NAME, f"{s.get('host')} was hosting but hasn't checked in for {minutes} "
                                                 "min (the PC may have crashed or lost power, or it is still uploading)."
                                                 f"\n\n{saved}\n\nAsk them first: what they did after that may only be "
                                                 "on their PC, and taking over means starting from that saved copy."
                                                 "\n\nTake over hosting anyway?", icon="warning"):
                return
            self.takeover = True
        if self.listening and not self.controller.running():     # (the last background check, so no pause here)
            return messagebox.showwarning(APP_NAME, "Something is already using this PC's server port (the old "
                                                    "Start SMP window?). Close it first.")
        problem = self.home.disk_problem()
        if problem:
            return messagebox.showwarning(APP_NAME, problem)
        if self.home.uses_tunnel(self.tools) and other_playit_running() and not self.stop_other_playit():
            return
        if not self.home.has_launcher():
            self.busy = "Downloading the Minecraft server..."
            settings = self.home.settings()
            self.in_background(lambda: self.home.install_server(settings.get("MC_VERSION") or "26.3", True),
                               lambda _r, error: self._launch(error))
            return
        self._launch(None)

    def stop_other_playit(self) -> bool:
        """A separate playit agent is running with the same tunnel identity: offer to stop it. True once it is gone."""
        if not messagebox.askyesno(APP_NAME, f"The separate playit app is running on this PC. {APP_NAME} runs its own, "
                                             "and two of them fight over the address.\n\nStop the separate one now?"):
            return False
        exe = shutil.which("playit") or os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"),
                                                     "playit_gg", "bin", "playit.exe")
        try:
            subprocess.run([exe, "stop"], capture_output=True, timeout=30,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.SubprocessError):
            pass
        for _ in range(10):
            if not other_playit_running():
                return True
            time.sleep(0.5)
        messagebox.showwarning(APP_NAME, "It couldn't be stopped from here. Quit playit from its tray icon (next to the "
                                         "clock), or run 'playit stop' in an administrator PowerShell, then try again.")
        return False

    def _launch(self, error):
        if error:
            self.busy = ""
            return messagebox.showerror(APP_NAME, f"Couldn't get the Minecraft server: {error}")
        self.busy, self.expect_exit = "", False
        self.log_start = log_size(self.home.log_path)
        try:
            proc = self.controller.start(takeover=getattr(self, "takeover", False))
            self.takeover = False
            self.job.add(proc)
        except Exception as exc:
            return messagebox.showerror(APP_NAME, f"Couldn't start: {exc}")
        self.say("Starting: taking the hosting lock, then getting the latest world. Watch the bar.", "info")
        self.render()

    def stop_hosting(self, then=None):
        if not self.listening:
            if not self.controller.running():
                if then:                                  # nothing is running, so there is nothing to stop
                    then()
                return
            if messagebox.askyesno(APP_NAME, "It's still getting ready (downloading the world). Cancel? Nothing is "
                                             "lost; the download starts over next time."):
                self.expect_exit = True
                if not self.job.terminate():
                    self.controller.kill()
                if then:
                    then()
            return
        self.busy = "Saving, uploading the world and handing over... Keep the PC on until this says Done."
        self.say("Stopping: saving the world and uploading it. Keep the PC on until it says Done.", "info")

        def done(result, error):
            if isinstance(error, TimeoutError):                  # a very slow upload: it is still working, just wait
                return self._stopping(then)
            ok, why = result if result else (False, str(error))
            if ok:
                return self._stopping(then)
            if "play" in why.lower() or "online" in why.lower():
                if messagebox.askyesno(APP_NAME, f"{why}\n\nStop anyway? Everyone online is kicked. The world is "
                                                 "still saved and uploaded."):
                    return self.in_background(self.panel.quit, lambda _r, _e: self._stopping(then))
            else:
                messagebox.showerror(APP_NAME, why or "The server didn't stop.")
            self.busy = ""
        self.in_background(self.panel.handover, done)

    def _stopping(self, then=None):
        self.expect_exit, self.quitting = True, then is not None
        self.busy = "Saving, uploading the world and handing over... Keep the PC on until this says done."
        if then:
            self._after_exit(then)

    def _after_exit(self, then):
        if self.hosting():
            self.root.after(1000, lambda: self._after_exit(then))
        else:
            then()

    def open_panel(self):
        webbrowser.open(f"http://127.0.0.1:{self.home.panel_port()}")

    def server_dialog(self):
        """Edit the server's name, description and picture, with a preview of the Minecraft server-list entry."""
        face = self.home.identity()
        win = tk.Toplevel(self.root)
        win.title("Server info")
        win.transient(self.root)
        win.resizable(False, False)
        dw, dh = 660, 580
        s = Scene(win, self.family, dw, dh)
        s.image(pixel.panel(dw, dh), 0, 0)
        s.text(24, 18, "Server info", 14, "#202020", bold=True)
        s.text(24, 48, "This is what players see in their Minecraft server list.", 10, pixel.LABEL, width=dw - 48)

        pic = {"mode": KEEP, "data": None}                   # KEEP, REMOVE, or the new 64x64 PNG bytes

        def current_icon():
            if isinstance(pic["data"], bytes):
                return pic["data"]
            if pic["mode"] == REMOVE:
                return None
            return identity.read_icon(self.home.server_dir)

        def icon_image(size):
            data = current_icon()
            img = Image.open(io.BytesIO(data)).convert("RGBA") if data else pixel.grass_block(64)
            return img.resize((size, size), Image.NEAREST)

        s.image(pixel.slot(140), 24, 86)
        big = s.canvas.create_image(30, 92, anchor="nw")
        Button(s, 24, 238, 150, 32, "Choose picture", lambda: choose(), size=10)
        Button(s, 24, 276, 150, 32, "Remove picture", lambda: remove(), size=10)

        name_var, desc_var = tk.StringVar(value=face["name"]), tk.StringVar(value=face["description"])
        s.text(190, 86, "Name", 10, pixel.LABEL)
        count_name = s.text(dw - 24, 86, "", 10, pixel.LABEL, anchor="ne")
        s.field(tk.Entry(s.canvas, textvariable=name_var, **s.entry_style(12)), 190, 108, dw - 214, 32)
        s.text(190, 158, "Description (optional)", 10, pixel.LABEL)
        count_desc = s.text(dw - 24, 158, "", 10, pixel.LABEL, anchor="ne")
        s.field(tk.Entry(s.canvas, textvariable=desc_var, **s.entry_style(12)), 190, 180, dw - 214, 32)
        s.text(190, 226, "Plain text only. The name is shown in bold.", 9, "#606060", width=dw - 214)

        s.text(24, 336, "Preview (how it looks in Minecraft)", 10, pixel.LABEL)
        s.image(pixel.text_field(dw - 48, 84), 24, 358)
        small = s.canvas.create_image(36, 368, anchor="nw")
        prev_name = s.text(116, 372, "", 12, "#ffffff", "#3f3f3f", width=dw - 160, bold=True)
        prev_desc = s.text(116, 400, "", 11, "#aaaaaa", width=dw - 160)
        s.text(24, 458, "It shows from the next time the server wakes up, and it is shared with everyone who hosts.",
               10, pixel.LABEL, width=dw - 48)
        note = s.text(24, 490, "", 10, "#aa0000", width=dw - 48)

        shown = {"picture": None}                            # the picture is only redrawn when it changes, not per keystroke

        def refresh(*_):
            name, desc = name_var.get(), desc_var.get()
            key = (pic["mode"], id(pic["data"]))
            if shown["picture"] != key:
                shown["picture"] = key
                s.canvas.itemconfig(big, image=s.photo(icon_image(128)))
                s.canvas.itemconfig(small, image=s.photo(icon_image(64)))
            prev_name.set(identity.clean(name, identity.MAX_NAME) or "(a name is needed)")
            prev_desc.set(identity.clean(desc, identity.MAX_DESCRIPTION))
            count_name.set(f"{len(name)}/{identity.MAX_NAME}",
                           "#aa0000" if len(name) > identity.MAX_NAME else pixel.LABEL)
            count_desc.set(f"{len(desc)}/{identity.MAX_DESCRIPTION}",
                           "#aa0000" if len(desc) > identity.MAX_DESCRIPTION else pixel.LABEL)

        def choose():
            path = filedialog.askopenfilename(parent=win, title="Choose a picture", filetypes=(
                ("Pictures", "*.png *.jpg *.jpeg *.gif *.bmp *.webp"), ("All files", "*.*")))
            if not path:
                return
            try:
                pic["data"], pic["mode"] = make_icon(path), "new"
            except ValueError as exc:
                return note.set(str(exc), "#aa0000")
            note.set("")
            refresh()

        def remove():
            pic["data"], pic["mode"] = None, REMOVE
            refresh()

        def save():
            if len(name_var.get()) > identity.MAX_NAME or len(desc_var.get()) > identity.MAX_DESCRIPTION:
                return note.set(f"The name can be {identity.MAX_NAME} characters at most and the description "
                                f"{identity.MAX_DESCRIPTION}. Shorten the one in red.", "#aa0000")
            picture = pic["data"] if isinstance(pic["data"], bytes) else (REMOVE if pic["mode"] == REMOVE else KEEP)
            try:
                self.home.save_identity(name_var.get(), desc_var.get(), picture)
            except ValueError as exc:
                return note.set(str(exc), "#aa0000")
            save_btn.set_enabled(False)
            note.set("Saving...", pixel.LABEL)

            def work():
                if self.listening:                           # the running controller uses it from now on
                    try:
                        self.panel.reload_identity()
                    except OSError:
                        pass
                try:
                    self.home.sync_identity(self.world())    # share it with the other hosts
                    return None
                except Exception as exc:
                    return str(exc)

            def done(problem, error):
                win.destroy()
                self.show_main()                             # the header shows the new name and picture
                if problem or error:
                    messagebox.showinfo(APP_NAME, "Saved on this PC. It couldn't be shared just now "
                                                  f"({problem or error}). It is shared automatically as soon as the "
                                                  "shared storage can be reached.")
            self.in_background(work, done)

        save_btn = Button(s, 24, dh - 64, 200, 40, "Save", save, primary=True)
        Button(s, 240, dh - 64, 160, 40, "Cancel", win.destroy)
        name_var.trace_add("write", refresh)
        desc_var.trace_add("write", refresh)
        refresh()

    def invite_dialog(self):
        remote = self.home.settings().get("SHARED_REMOTE", "")
        win = tk.Toplevel(self.root)
        win.title("Invite a friend")
        win.transient(self.root)
        win.resizable(False, False)
        dw, dh = 600, 548
        s = Scene(win, self.family, dw, dh)
        s.image(pixel.panel(dw, dh), 0, 0)
        s.text(24, 20, "Invite a friend", 14, "#202020", bold=True)
        r2 = self.home.storage_kind()["type"] == "r2"
        if r2:
            intro = ("In Cloudflare (R2 > Manage API tokens), make an API token for your friend: Object Read & Write, "
                     "this bucket only. Paste its Access Key ID and Secret Access Key here.")
        else:
            intro = ("On the Backblaze website, make an application key for your friend (that bucket only, Read and "
                     "Write). Paste it here.")
        text = s.text(24, 56, s.fit(intro, 10, dw - 48, 4), 10, pixel.LABEL)
        y = s.canvas.bbox(text.main)[3] + 20                          # start the fields below however long the intro is
        fields = {}
        for label, key, default, secret in (("Bucket", "bucket", remote.split(":", 1)[1]
                                             if remote.startswith(REMOTE + ":") else "", False),
                                            ("Access Key ID" if r2 else "keyID", "key_id", "", False),
                                            ("Secret Access Key" if r2 else "applicationKey", "key", "", True)):
            s.text(24, y, label, 10, pixel.LABEL)
            var = tk.StringVar(value=default)
            s.field(tk.Entry(s.canvas, textvariable=var, show="*" if secret else "", **s.entry_style(11)),
                    24, y + 22, dw - 48, 30)
            fields[key], y = var, y + 68
        Button(s, 24, y, 200, 38, "Create invite", lambda: create(), primary=True)
        y += 38 + 22
        s.text(24, y, "Send this code to your friend privately (it is also copied).", 10, pixel.LABEL)
        out = s.field(tk.Text(s.canvas, wrap="char", padx=8, pady=6, **s.entry_style(9)), 24, y + 22, dw - 48, 86)
        note = s.text(24, y + 22 + 86 + 12, "", 10, "#aa0000", width=dw - 48)

        def create():
            try:
                code = self.home.make_invite_for(fields["bucket"].get(), fields["key_id"].get(), fields["key"].get())
            except invites.InviteError as exc:
                return note.set(s.fit(str(exc), 10, dw - 48, 2), "#aa0000")
            out.delete("1.0", "end")
            out.insert("1.0", code)
            self.copy(code)
            note.set("Copied! Never post it in a public channel: anyone with it can host your world.", "#1e6b1e")

    # -- closing and the tray ---------------------------------------------------------------------------
    def on_close(self):
        if not self.hosting():
            return self.quit()
        answer = messagebox.askyesnocancel(APP_NAME, "You're hosting. Keep hosting in the background?\n\n"
                                                     "Yes: hide to the tray, friends can keep playing.\n"
                                                     "No: stop hosting (upload and hand over), then close.")
        if answer is True:
            self.hide_to_tray()
        elif answer is False:
            self.stop_hosting(then=self.quit)

    def shutdown_attempt(self):
        """Windows is about to shut down (or sign out) while this PC hosts: bring the window up and offer to stop first."""
        if not self.hosting() or self.busy:
            return
        self.show_window()
        if messagebox.askyesno(APP_NAME, "This PC is shutting down, but you're still hosting.\n\nIf it switches off now, "
                                         "the latest changes to the world are not uploaded and the next host starts "
                                         "from an older copy.\n\nStop hosting now (upload and hand over)? "
                                         "If Windows is asking, choose Cancel there first."):
            self.stop_hosting()

    def hide_to_tray(self):
        try:
            import pystray
        except ImportError:
            return self.root.iconify()
        if self.tray is None:
            menu = pystray.Menu(pystray.MenuItem(f"Open {APP_NAME}", lambda: self.later(self.show_window), default=True),
                                pystray.MenuItem("Stop hosting and quit",
                                                 lambda: self.later(lambda: self.stop_hosting(then=self.quit))))
            self.tray = pystray.Icon("smp-host", pixel.grass_block(64), APP_NAME, menu)
            self.tray.run_detached()
            self.root.after(800, self._tray_hint)
        self.root.withdraw()

    def _tray_hint(self):
        try:
            self.tray.notify("Still running. Click this icon (next to the clock) to open it again, "
                             "or right-click it to quit.", APP_NAME)
        except Exception:
            pass                                          # a notification is only a courtesy

    def show_window(self):
        self.root.deiconify()
        self.root.lift()
        if self.scene is not None and self.main_ready:
            self.refresh_shared()                         # it didn't look while hidden: catch up now

    def quit(self):
        self.guard.close()
        if self.tray is not None:
            self.tray.stop()
        self.root.destroy()


LOG_LINE = re.compile(r"^\d{4}-\d\d-\d\d (\d\d:\d\d:\d\d),\d+ (\w+) ([\w.]+): (.*)$")
QUIET_LOGGERS = ("aiohttp.access", "discord.")


def friendly_log(text: str) -> str:
    """'2026-10-17 16:46:10,027 INFO main: Taking the lock' -> '16:46:10  Taking the lock' (and warnings marked)."""
    out = []
    for line in text.splitlines():
        m = LOG_LINE.match(line)
        if not m:
            if line.strip():
                out.append("          " + line)            # continuation of the previous message (tracebacks etc.)
            continue
        clock, level, name, message = m.groups()
        if name.startswith(QUIET_LOGGERS) and level == "INFO":
            continue
        mark = {"WARNING": "!  ", "ERROR": "!! ", "CRITICAL": "!! "}.get(level, "")
        out.append(f"{clock}  {mark}{message}")
    return "\n".join(out) + "\n" if out else ""


def other_playit_running() -> bool:
    """A playit agent that isn't ours (ours only runs while hosting, which is checked before this)."""
    if sys.platform != "win32":
        return False
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq playitd.exe", "/NH"], capture_output=True, text=True,
                             timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except Exception:
        return False
    return "playitd.exe" in out.lower()


_MUTEX = None


def already_open() -> bool:
    global _MUTEX
    if sys.platform != "win32":
        return False
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateMutexW.restype = ctypes.c_void_p
    _MUTEX = k32.CreateMutexW(None, False, "Local\\SMPHostSingleInstance")   # held until the app exits
    return ctypes.get_last_error() == 183                                     # ERROR_ALREADY_EXISTS


def main():
    if already_open():
        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo(APP_NAME, f"{APP_NAME} is already open (look in the tray, next to the clock).")
        return
    if sys.platform == "win32":
        try:        # its own taskbar identity, so the window isn't grouped with other Python programs
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("SMPHost.App")
        except Exception:
            pass
    family = pixel.register_fonts()
    root = tk.Tk()
    App(root, Home(), family)
    root.mainloop()


if __name__ == "__main__":
    main()
