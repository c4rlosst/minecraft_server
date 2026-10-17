"""SMP Host launcher. The shortcut runs this with the bundled, signed pythonw.exe (no console window, and no custom
.exe at all). It only starts the window; the controller runs as a background process of the same Python.

If anything goes wrong before the window exists there is no console to show it, so the problem is written to
%LOCALAPPDATA%\\SMP Host\\crash.log and shown in a message box."""
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
for candidate in (os.path.join(HERE, "controller"), os.path.join(os.path.dirname(HERE), "controller")):
    if os.path.isdir(os.path.join(candidate, "app")):
        sys.path.insert(0, candidate)          # installed layout, or running from the repo's desktop folder
        break


def report(problem: str) -> None:
    where = ""
    try:
        folder = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "SMP Host")
        os.makedirs(folder, exist_ok=True)
        where = os.path.join(folder, "crash.log")
        with open(where, "a", encoding="utf-8") as f:
            f.write(problem + "\n")
    except OSError:
        pass
    try:
        import tkinter
        from tkinter import messagebox
        root = tkinter.Tk()
        root.withdraw()
        last = (problem.strip().splitlines() or ["unknown error"])[-1]
        messagebox.showerror("SMP Host", f"SMP Host hit a problem and has to close.\n\n{last}\n\n"
                                         + (f"Details: {where}" if where else ""))
    except Exception:
        pass


if __name__ == "__main__":
    try:
        from app.desktop.gui import main
        main()
    except Exception:
        report(traceback.format_exc())
