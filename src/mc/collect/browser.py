"""Playwright persistent context + insonga o'xshash xatti-harakat.

Burner FB akkaunt profili `data/fb_profile/` da saqlanadi -- bir marta qo'lda login
qilinadi, keyin cookie shu yerda qoladi.
"""

from __future__ import annotations

import random
import time
from contextlib import contextmanager
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

from .. import db
from .display import Hidden

PROFILE_DIR = db.DATA / "fb_profile"

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# FB akkauntni to'sib qo'yganini bildiruvchi belgilar
CHECKPOINT_MARKERS = [
    "/checkpoint/",
    "login/device-based",
    "We suspend accounts",
    "Your account has been",
    "Confirm Your Identity",
    "temporarily blocked",
]


class SessionDead(RuntimeError):
    """FB sessiya tugagan yoki akkaunt checkpoint'ga tushgan."""


@contextmanager
def browser(headless: bool = False, window: str = "auto"):
    """Brauzer konteksti.

    `window` -- oynani yashirish usuli (`display.py` ga qarang). Linux'da Xvfb
    ishlatilsa brauzer haqiqiy headful bo'lib qoladi, lekin ekranga chiqmaydi:
    FB uchun eng xavfsiz kombinatsiya.
    """
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    with Hidden(window) as hide, sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=headless or hide.headless,
            user_agent=UA,
            viewport={"width": 1440, "height": 900},
            locale="en-US",
            timezone_id="America/New_York",
            args=["--disable-blink-features=AutomationControlled", *hide.args],
        )
        # navigator.webdriver ni yashirish
        ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
        )
        hide.after_launch()
        try:
            yield ctx
        finally:
            ctx.close()


def pause(rng: list[float]) -> None:
    time.sleep(random.uniform(rng[0], rng[1]))


def check_alive(page: Page) -> None:
    """Sessiya tirikligini tekshiradi; o'lgan bo'lsa SessionDead ko'taradi."""
    url = page.url
    if "/login" in url or "/checkpoint" in url:
        raise SessionDead(f"FB checkpoint yoki logout: {url}")
    try:
        body = page.inner_text("body", timeout=5000)[:4000]
    except Exception:
        return
    for marker in CHECKPOINT_MARKERS:
        if marker.lower() in body.lower() or marker in url:
            raise SessionDead(f"FB checkpoint belgisi topildi: {marker!r}")


def human_scroll(page: Page, pause_rng: list[float]) -> None:
    """Bir marta insonga o'xshash scroll -- tasodifiy masofa va pauza."""
    delta = random.randint(500, 1100)
    steps = random.randint(3, 6)
    for _ in range(steps):
        page.mouse.wheel(0, delta // steps)
        time.sleep(random.uniform(0.15, 0.45))
    pause(pause_rng)


def is_logged_in(page: Page) -> bool:
    try:
        page.goto("https://www.facebook.com/", wait_until="domcontentloaded", timeout=45000)
    except Exception:
        return False
    time.sleep(3)
    return "/login" not in page.url and "checkpoint" not in page.url
