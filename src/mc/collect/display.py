"""Brauzer oynasini ekrandan yashirish -- har bir OS uchun boshqa yo'l bilan.

Nega headless emas: FB headless Chromium'ni oson aniqlaydi (bu loyihada
`headless: false` ataylab tanlangan). Shuning uchun iloji boricha **haqiqiy**
brauzer ishlaydi, faqat ko'zga ko'rinmaydi.

| OS      | Usul                        | Kafolat                          |
|---------|-----------------------------|----------------------------------|
| Linux   | Xvfb virtual ekran          | to'liq -- oyna umuman chizilmaydi |
| macOS   | ilovani yashirish (osascript)| yaxshi, lekin Dock'da qoladi     |
| Windows | headless                    | to'liq, ban riski biroz yuqori   |

`config.yaml` -> `collect.window`: `auto` (default) | `hidden` | `visible`.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import time

# Oyna ko'rinmayotganda Chromium renderer'ni "fon" deb hisoblab sekinlashtiradi --
# u holda scroll ishlamay qoladi. Bu bayroqlar buni to'xtatadi.
NO_THROTTLE = [
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
    "--disable-background-timer-throttling",
]


def xvfb_available() -> bool:
    return shutil.which("Xvfb") is not None


def describe() -> str:
    """`mc doctor` uchun bir qatorli tushuntirish."""
    osname = platform.system()
    if osname == "Linux":
        return ("Xvfb virtual ekran" if xvfb_available()
                else "Xvfb yo'q — `sudo apt install xvfb`, aks holda oyna ko'rinadi")
    if osname == "Darwin":
        return "macOS: oyna yashiriladi (Dock'da qoladi)"
    return "headless rejim"


class Hidden:
    """Yashirish strategiyasi.

    `headless` -- Playwright'ga beriladi; `args` -- Chromium bayroqlari;
    `after_launch(ctx)` -- brauzer ochilgandan keyin bajariladigan ish.
    """

    def __init__(self, mode: str = "auto") -> None:
        self.mode = mode
        self.headless = False
        self.args: list[str] = []
        self._display = None
        self._note = ""

    @property
    def note(self) -> str:
        return self._note

    def __enter__(self) -> "Hidden":
        if self.mode == "visible":
            self._note = "oyna ko'rinadi (config: window=visible)"
            return self

        osname = platform.system()
        if osname == "Linux":
            if os.environ.get("DISPLAY", "").startswith(":9") or not _has_screen():
                self._note = "ekran yo'q — brauzer allaqachon ko'rinmaydi"
                return self
            if xvfb_available():
                try:
                    from pyvirtualdisplay import Display

                    self._display = Display(visible=False, size=(1440, 900))
                    self._display.start()
                    self._note = f"Xvfb virtual ekran ({os.environ.get('DISPLAY')})"
                    self.args = list(NO_THROTTLE)
                    return self
                except Exception as e:
                    self._note = f"Xvfb ishga tushmadi ({e}) — oyna ko'rinadi"
                    return self
            self._note = ("Xvfb topilmadi (`sudo apt install xvfb`) — oyna ko'rinadi")
            return self

        if osname == "Darwin":
            self.args = list(NO_THROTTLE)
            self._note = "macOS: oyna yashiriladi"
            return self

        # Windows va boshqalar: ishonchli yagona yo'l
        self.headless = True
        self._note = "headless (bu OS'da oynani yashirib bo'lmaydi)"
        return self

    def after_launch(self) -> None:
        """macOS'da brauzer ko'ringandan keyin uni yashiramiz."""
        if platform.system() != "Darwin" or self.mode == "visible":
            return
        time.sleep(1.5)
        for app in ("Chromium", "Google Chrome for Testing", "Google Chrome"):
            try:
                subprocess.run(
                    ["osascript", "-e",
                     f'tell application "System Events" to set visible of '
                     f'(first process whose name is "{app}") to false'],
                    capture_output=True, timeout=10,
                )
            except Exception:
                continue

    def __exit__(self, *exc) -> None:
        if self._display is not None:
            try:
                self._display.stop()
            except Exception:
                pass
            self._display = None


def _has_screen() -> bool:
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
