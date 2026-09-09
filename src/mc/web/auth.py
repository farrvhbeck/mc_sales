"""Dashboard uchun oddiy parol himoyasi.

Faqat `DASHBOARD_PASSWORD` .env da bo'lsa yoqiladi. Bo'lmasa dashboard
himoyasiz qoladi -- bu localhost uchun to'g'ri, lekin internetga chiqarishdan
oldin parol MAJBURIY (`mc share` buni tekshiradi).

Sessiya server tomonda saqlanmaydi: cookie parol asosidagi kalit bilan
imzolanadi, ya'ni parol o'zgarsa hamma sessiya bekor bo'ladi.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

COOKIE = "mc_session"
MAX_AGE = 30 * 24 * 3600          # 30 kun
OPEN_PATHS = ("/login", "/static/")


def password() -> str | None:
    from .. import db

    db.load_env()
    return os.environ.get("DASHBOARD_PASSWORD") or None


def _sign(payload: str, secret: str) -> str:
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]


def make_token(secret: str) -> str:
    issued = str(int(time.time()))
    return f"{issued}.{_sign(issued, secret)}"


def valid_token(token: str | None, secret: str) -> bool:
    if not token or "." not in token:
        return False
    issued, sig = token.rsplit(".", 1)
    if not hmac.compare_digest(sig, _sign(issued, secret)):
        return False
    try:
        return time.time() - int(issued) < MAX_AGE
    except ValueError:
        return False


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        secret = password()
        if not secret or request.url.path.startswith(OPEN_PATHS):
            return await call_next(request)

        if valid_token(request.cookies.get(COOKIE), secret):
            return await call_next(request)

        if request.method == "GET":
            nxt = request.url.path
            if request.url.query:
                nxt += "?" + request.url.query
            return RedirectResponse(f"/login?next={nxt}", status_code=303)
        return RedirectResponse("/login", status_code=303)


LOGIN_PAGE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark"><title>Sign in · MC Leads</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<link rel="stylesheet" href="/static/app.css">
</head><body style="display:grid;place-items:center;min-height:100vh;padding:20px">
  <form method="post" action="/login" style="width:min(340px,100%);text-align:center">
    <input type="hidden" name="next" value="__NEXT__">
    <div style="width:46px;height:46px;border-radius:13px;margin:0 auto 16px;
                background:linear-gradient(160deg,var(--blue),var(--indigo));
                display:grid;place-items:center;font-size:22px">🚛</div>
    <h1 style="font-size:22px;font-weight:700;letter-spacing:-.03em;margin:0 0 4px">MC Leads</h1>
    <p style="color:var(--label-2);margin:0 0 20px">Enter the password to continue.</p>
    <label class="field" style="width:100%">
      <input type="password" name="password" placeholder="Password" autofocus
             autocomplete="current-password" style="text-align:center">
    </label>
    __ERROR__
    <button class="btn btn-primary" style="width:100%;margin-top:12px;justify-content:center">
      Sign in</button>
  </form>
</body></html>"""


def login_page(next_url: str = "/", error: bool = False) -> HTMLResponse:
    err = ('<p style="color:var(--red);margin:10px 0 0;font-size:13px">'
           'That password is not right.</p>') if error else ""
    safe_next = next_url if next_url.startswith("/") else "/"
    html = (LOGIN_PAGE
            .replace("__NEXT__", safe_next.replace('"', "&quot;"))
            .replace("__ERROR__", err))
    return HTMLResponse(html, status_code=401 if error else 200)


def sign_in(secret: str, next_url: str) -> RedirectResponse:
    target = next_url if next_url.startswith("/") else "/"
    r = RedirectResponse(target, status_code=303)
    r.set_cookie(COOKIE, make_token(secret), max_age=MAX_AGE,
                 httponly=True, samesite="lax")
    return r


def new_password() -> str:
    """Odam o'qiy oladigan, lekin taxmin qilib bo'lmaydigan parol."""
    words = ("amber anchor basil cedar delta ember falcon granite harbor indigo "
             "jasper kettle lantern marble nectar onyx pebble quartz rustic saffron "
             "timber umber velvet walnut zephyr").split()
    return "-".join(secrets.choice(words) for _ in range(3)) + "-" + str(secrets.randbelow(90) + 10)
