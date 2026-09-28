"""Native folder picker for local WebUI (safe off the uvicorn worker thread).

macOS AppKit/Tk must not create NSWindow from a non-main thread; uvicorn handles
HTTP on worker threads, so we never call tkinter in-process. Prefer OS dialogs,
then a short-lived subprocess whose main thread runs tkinter.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass


PROMPT = "ケース親フォルダを選択（manuscript/ と data/ を含む）"


@dataclass(frozen=True)
class PickResult:
    path: str | None = None
    cancelled: bool = False
    error: str | None = None


def pick_folder() -> PickResult:
    system = platform.system()
    if system == "Darwin":
        result = _pick_macos_osascript()
        if result is not None:
            return result
    elif system == "Linux":
        result = _pick_linux_zenity_or_kdialog()
        if result is not None:
            return result

    return _pick_tkinter_subprocess()


def _pick_macos_osascript() -> PickResult | None:
    if not shutil.which("osascript"):
        return None
    script = (
        f'set theFolder to choose folder with prompt "{PROMPT}"\n'
        "POSIX path of theFolder"
    )
    try:
        proc = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            check=False,
            timeout=600,
        )
    except subprocess.TimeoutExpired:
        return PickResult(error="フォルダ選択がタイムアウトしました。")
    except OSError as exc:
        return PickResult(error=f"フォルダ選択を起動できませんでした: {exc}")

    if proc.returncode != 0:
        # User cancel typically exits non-zero with empty/stderr from AppleScript
        err = (proc.stderr or "").strip().lower()
        if "user canceled" in err or "user cancelled" in err or not (proc.stdout or "").strip():
            return PickResult(cancelled=True)
        return PickResult(error=(proc.stderr or proc.stdout or "フォルダ選択に失敗しました").strip())

    path = (proc.stdout or "").strip()
    if not path:
        return PickResult(cancelled=True)
    return PickResult(path=path.rstrip("/"))


def _pick_linux_zenity_or_kdialog() -> PickResult | None:
    if shutil.which("zenity"):
        try:
            proc = subprocess.run(
                [
                    "zenity",
                    "--file-selection",
                    "--directory",
                    f"--title={PROMPT}",
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=600,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return PickResult(error=f"フォルダ選択を起動できませんでした: {exc}")
        if proc.returncode != 0:
            return PickResult(cancelled=True)
        path = (proc.stdout or "").strip()
        return PickResult(path=path) if path else PickResult(cancelled=True)

    if shutil.which("kdialog"):
        try:
            proc = subprocess.run(
                ["kdialog", "--getexistingdirectory", ".", "--title", PROMPT],
                capture_output=True,
                text=True,
                check=False,
                timeout=600,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return PickResult(error=f"フォルダ選択を起動できませんでした: {exc}")
        if proc.returncode != 0:
            return PickResult(cancelled=True)
        path = (proc.stdout or "").strip()
        return PickResult(path=path) if path else PickResult(cancelled=True)

    return None


_TK_SCRIPT = r"""
import sys
try:
    import tkinter as tk
    from tkinter import filedialog
except Exception as exc:
    print(f"ERROR:{exc}", file=sys.stderr)
    sys.exit(2)
root = tk.Tk()
root.withdraw()
try:
    root.attributes("-topmost", True)
except Exception:
    pass
path = filedialog.askdirectory(title=%r)
root.destroy()
if path:
    print(path)
    sys.exit(0)
sys.exit(1)
""" % (PROMPT,)


def _pick_tkinter_subprocess() -> PickResult:
    try:
        proc = subprocess.run(
            [sys.executable, "-c", _TK_SCRIPT],
            capture_output=True,
            text=True,
            check=False,
            timeout=600,
        )
    except subprocess.TimeoutExpired:
        return PickResult(error="フォルダ選択がタイムアウトしました。")
    except OSError as exc:
        return PickResult(
            error=(
                "フォルダ選択ダイアログを開けませんでした"
                f"（{exc}）。パスを手入力してください。"
            )
        )

    if proc.returncode == 0:
        path = (proc.stdout or "").strip()
        if path:
            return PickResult(path=path)
        return PickResult(cancelled=True)
    if proc.returncode == 1:
        return PickResult(cancelled=True)
    err = (proc.stderr or "").strip() or "フォルダ選択に失敗しました"
    if err.startswith("ERROR:"):
        err = err[len("ERROR:") :]
    return PickResult(
        error=f"{err}。パスを手入力してください。",
    )
