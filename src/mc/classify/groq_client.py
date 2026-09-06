"""Groq chat client: bir nechta API key, retry, rate-limit backoff, kunlik budjet.

Groq free tier limitlari **akkaunt boshiga** (o'lchangan 2026-09-06: 1000 RPD,
8000 TPM). Shuning uchun bir nechta akkauntning key'i qo'shilsa, limit shuncha
barobar oshadi. Har bir key alohida hisoblanadi: o'z daqiqalik chelagi, o'z
kunlik budjeti. Biri tugasa yoki 429 bersa, avtomatik keyingisiga o'tadi.

`.env` da:
    GROQ_API_KEY=gsk_...
    GROQ_API_KEY_2=gsk_...        # yoki GROQ_API_KEYS=gsk_a,gsk_b
"""

from __future__ import annotations

import json
import os
import random
import re
import time
from typing import Any

import httpx

from .. import db


class BudgetExhausted(RuntimeError):
    """Barcha key'larning kunlik budjeti tugadi -- qolgani ertaga."""


def _load_keys() -> list[str]:
    db.load_env()
    keys: list[str] = []
    if os.environ.get("GROQ_API_KEY"):
        keys.append(os.environ["GROQ_API_KEY"].strip())
    for raw in (os.environ.get("GROQ_API_KEYS") or "").split(","):
        if raw.strip():
            keys.append(raw.strip())
    for name, val in os.environ.items():
        if re.fullmatch(r"GROQ_API_KEY_\d+", name) and val.strip():
            keys.append(val.strip())

    seen, out = set(), []
    for k in keys:
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


class _Key:
    """Bitta API key va uning mahalliy hisoblagichlari."""

    def __init__(self, label: str, secret: str) -> None:
        self.label = label
        self.secret = secret
        self.minute_start = time.time()
        self.minute_tokens = 0
        self.cooldown_until = 0.0     # 429 dan keyin

    def headroom(self, tpm_limit: int) -> int:
        now = time.time()
        if now - self.minute_start >= 60:
            self.minute_start = now
            self.minute_tokens = 0
        return int(tpm_limit * 0.9) - self.minute_tokens

    def available(self) -> bool:
        return time.time() >= self.cooldown_until


class GroqClient:
    def __init__(self, cfg: dict) -> None:
        secrets = _load_keys()
        if not secrets:
            raise RuntimeError("GROQ_API_KEY .env da yo'q")
        self.cfg = cfg["llm"]
        self.keys = [_Key(f"k{i}", s) for i, s in enumerate(secrets, 1)]
        self.client = httpx.Client(base_url=self.cfg["base_url"], timeout=120)

    # --- budjet ---------------------------------------------------------

    def _spent(self, label: str | None = None) -> int:
        day = time.strftime("%Y-%m-%d")
        sql = ("SELECT COALESCE(SUM(prompt_tokens + completion_tokens), 0) AS t "
               "FROM llm_usage WHERE day = ?")
        args: list = [day]
        if label:
            sql += " AND key_label = ?"
            args.append(label)
        with db.connect() as conn:
            return conn.execute(sql, args).fetchone()["t"] or 0

    def spent_today(self) -> int:
        return self._spent()

    def remaining_today(self) -> int:
        """Barcha key'lar bo'yicha qolgan budjet."""
        per = self.cfg["daily_token_budget"]
        return sum(max(0, per - self._spent(k.label)) for k in self.keys)

    def budget_report(self) -> list[dict]:
        per = self.cfg["daily_token_budget"]
        return [{"key": k.label, "spent": self._spent(k.label),
                 "remaining": max(0, per - self._spent(k.label))} for k in self.keys]

    def _record(self, label: str, model: str, usage: dict) -> None:
        day = time.strftime("%Y-%m-%d")
        pt = usage.get("prompt_tokens", 0)
        ct = usage.get("completion_tokens", 0)
        with db.connect() as conn:
            conn.execute(
                """INSERT INTO llm_usage (day, model, key_label, calls,
                                          prompt_tokens, completion_tokens)
                   VALUES (?, ?, ?, 1, ?, ?)
                   ON CONFLICT(day, model, key_label) DO UPDATE SET
                     calls = llm_usage.calls + 1,
                     prompt_tokens = llm_usage.prompt_tokens + excluded.prompt_tokens,
                     completion_tokens = llm_usage.completion_tokens + excluded.completion_tokens""",
                (day, model, label, pt, ct),
            )

    # --- key tanlash ----------------------------------------------------

    def _pick(self, est_tokens: int) -> _Key:
        """Budjeti va daqiqalik joyi bor key. Hech biri bo'sh bo'lmasa kutadi."""
        per = self.cfg["daily_token_budget"]
        tpm = self.cfg["tpm_limit"]

        while True:
            usable = [k for k in self.keys
                      if k.available() and self._spent(k.label) < per]
            if not usable:
                if all(self._spent(k.label) >= per for k in self.keys):
                    raise BudgetExhausted(
                        f"Barcha {len(self.keys)} key'ning kunlik budjeti tugadi "
                        f"({per} token/key)")
                time.sleep(min(k.cooldown_until - time.time()
                               for k in self.keys) + 0.5)
                continue

            # Eng ko'p bo'sh joyi bori
            best = max(usable, key=lambda k: k.headroom(tpm))
            if best.headroom(tpm) >= est_tokens:
                best.minute_tokens += est_tokens
                return best

            # Hammasi to'lgan -- eng erta bo'shaydigan daqiqani kutamiz
            wait = min(60 - (time.time() - k.minute_start) for k in usable) + 1
            time.sleep(max(1.0, wait))

    # --- chaqiruv -------------------------------------------------------

    def chat(
        self,
        model: str,
        system: str,
        user: str,
        json_schema: dict | None = None,
        max_tokens: int = 1200,
        reasoning_effort: str | None = None,
    ) -> dict[str, Any]:
        est = (len(system) + len(user)) // 3 + max_tokens

        body: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0,
            "max_tokens": max_tokens,
        }
        # gpt-oss modellari reasoning token sarflaydi; "low" bo'lmasa javob
        # max_tokens ichiga sig'may qoladi va bo'sh qaytadi.
        if reasoning_effort:
            body["reasoning_effort"] = reasoning_effort
        if json_schema:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "extraction", "strict": True, "schema": json_schema},
            }
        else:
            body["response_format"] = {"type": "json_object"}

        last_err: Exception | None = None
        for attempt in range(self.cfg["max_retries"]):
            key = self._pick(est)
            try:
                r = self.client.post(
                    "/chat/completions", json=body,
                    headers={"Authorization": f"Bearer {key.secret}"},
                )
            except httpx.HTTPError as e:
                last_err = e
                time.sleep(2**attempt + random.random())
                continue

            if r.status_code == 429:
                # Shu key charchadi -- boshqasiga o'tamiz
                retry_after = float(r.headers.get("retry-after", 0) or 0)
                key.cooldown_until = time.time() + max(retry_after, 5.0)
                last_err = RuntimeError(f"{key.label}: 429")
                continue
            if r.status_code == 401:
                key.cooldown_until = time.time() + 86400   # noto'g'ri key
                last_err = RuntimeError(f"{key.label}: 401 — key yaroqsiz")
                continue
            if r.status_code >= 500:
                time.sleep(2**attempt + random.random())
                continue
            if r.status_code != 200:
                raise RuntimeError(f"Groq {r.status_code}: {r.text[:400]}")

            data = r.json()
            self._record(key.label, model, data.get("usage", {}))
            content = data["choices"][0]["message"]["content"]
            try:
                return json.loads(content)
            except json.JSONDecodeError:
                # Ba'zan model matn ichiga JSON qo'yadi
                start = content.find("{") if "{" in content else content.find("[")
                end = max(content.rfind("}"), content.rfind("]"))
                if start >= 0 and end > start:
                    return json.loads(content[start : end + 1])
                raise

        raise RuntimeError(f"Groq qayta urinishlar tugadi: {last_err}")
