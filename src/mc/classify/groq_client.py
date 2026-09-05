"""Groq chat client: retry, rate-limit backoff, kunlik token budjeti.

Free tier o'lchangan (2026-09-05): 1000 RPD, 8000 TPM har bir model uchun.
TPM chegarasi tor, shuning uchun token sarfini o'zimiz ham hisoblaymiz.
"""

from __future__ import annotations

import json
import os
import random
import time
from typing import Any

import httpx

from .. import db


class BudgetExhausted(RuntimeError):
    """Kunlik token budjeti tugadi -- qolgani ertaga."""


class GroqClient:
    def __init__(self, cfg: dict) -> None:
        db.load_env()
        key = os.environ.get("GROQ_API_KEY")
        if not key:
            raise RuntimeError("GROQ_API_KEY .env da yo'q")
        self.cfg = cfg["llm"]
        self.client = httpx.Client(
            base_url=self.cfg["base_url"],
            headers={"Authorization": f"Bearer {key}"},
            timeout=120,
        )
        self._minute_start = time.time()
        self._minute_tokens = 0

    # --- budjet ---------------------------------------------------------

    def spent_today(self) -> int:
        day = time.strftime("%Y-%m-%d")
        with db.connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(prompt_tokens + completion_tokens), 0) AS t "
                "FROM llm_usage WHERE day = ?",
                (day,),
            ).fetchone()
        return row["t"] or 0

    def remaining_today(self) -> int:
        return max(0, self.cfg["daily_token_budget"] - self.spent_today())

    def _record(self, model: str, usage: dict) -> None:
        day = time.strftime("%Y-%m-%d")
        pt = usage.get("prompt_tokens", 0)
        ct = usage.get("completion_tokens", 0)
        with db.connect() as conn:
            conn.execute(
                """INSERT INTO llm_usage (day, model, calls, prompt_tokens, completion_tokens)
                   VALUES (?, ?, 1, ?, ?)
                   ON CONFLICT(day, model) DO UPDATE SET
                     calls = llm_usage.calls + 1,
                     prompt_tokens = llm_usage.prompt_tokens + excluded.prompt_tokens,
                     completion_tokens = llm_usage.completion_tokens + excluded.completion_tokens""",
                (day, model, pt, ct),
            )

    def _throttle(self, est_tokens: int) -> None:
        """TPM chegarasiga urilmaslik uchun mahalliy tormoz."""
        now = time.time()
        if now - self._minute_start >= 60:
            self._minute_start = now
            self._minute_tokens = 0
        if self._minute_tokens + est_tokens > self.cfg["tpm_limit"] * 0.9:
            wait = 60 - (now - self._minute_start) + 1
            if wait > 0:
                time.sleep(wait)
            self._minute_start = time.time()
            self._minute_tokens = 0
        self._minute_tokens += est_tokens

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
        if self.remaining_today() <= 0:
            raise BudgetExhausted(
                f"Kunlik budjet ({self.cfg['daily_token_budget']} token) tugadi"
            )

        est = (len(system) + len(user)) // 3 + max_tokens
        self._throttle(est)

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
            try:
                r = self.client.post("/chat/completions", json=body)
            except httpx.HTTPError as e:
                last_err = e
                time.sleep(2**attempt + random.random())
                continue

            if r.status_code == 429:
                retry_after = float(r.headers.get("retry-after", 0) or 0)
                time.sleep(max(retry_after, 2**attempt) + random.random())
                continue
            if r.status_code >= 500:
                time.sleep(2**attempt + random.random())
                continue
            if r.status_code != 200:
                raise RuntimeError(f"Groq {r.status_code}: {r.text[:400]}")

            data = r.json()
            self._record(model, data.get("usage", {}))
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
