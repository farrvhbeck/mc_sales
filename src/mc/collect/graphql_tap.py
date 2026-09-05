"""`POST /api/graphql/` javoblarini ushlab, xom JSON sifatida to'playdi.

FB bitta HTTP javobda bir nechta JSON obyektini qator-qator (NDJSON) qaytaradi --
shuning uchun har bir qatorni alohida parse qilamiz.
"""

from __future__ import annotations

import json
from typing import Any

from playwright.sync_api import Page, Response


class GraphQLTap:
    def __init__(self) -> None:
        self.payloads: list[Any] = []
        self._seen: set[int] = set()

    def attach(self, page: Page) -> None:
        page.on("response", self._on_response)

    def _on_response(self, resp: Response) -> None:
        if "/api/graphql" not in resp.url:
            return
        try:
            body = resp.text()
        except Exception:
            return
        for line in body.splitlines():
            line = line.strip()
            if not line or not line.startswith("{"):
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            h = hash(line)
            if h in self._seen:
                continue
            self._seen.add(h)
            self.payloads.append(obj)

    def drain(self) -> list[Any]:
        out, self.payloads = self.payloads, []
        return out
