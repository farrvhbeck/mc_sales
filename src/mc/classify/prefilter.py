"""T0 -- bepul regex filtri. Shovqinning katta qismini LLM'gacha o'ldiradi."""

from __future__ import annotations

import re

# Sotish/olish niyatini bildiruvchi signal
SIGNAL = re.compile(
    r"\b(sell|selling|sale|sold)\b"
    r"|\b(buy|buying|buyer|purchase|purchasing|interested)\b"
    r"|\blooking\s+for\b|\bneed\b|\bwant(ed)?\b"
    r"|\b(mc|dot|authority|llc)\b\s*[#:]?\s*\d{5,}"
    r"|\bmc\s*(number|#)|\bdot\s*(number|#)"
    r"|\bauthority\b"
    r"|\$\s*\d|\b\d+\s*k\b"
    r"|\bdm\b|\binbox\b|\bpm\s+me\b",
    re.IGNORECASE,
)

# Aniq shovqin: reklama, tabrik, savol-javob bo'lmagan chuchmal gaplar
NOISE = re.compile(
    r"^\s*(thanks?|thank\s+you|congrats?|congratulations|good\s+luck|nice|ok|okay|"
    r"yes|no|lol|\W*)\s*[.!]*\s*$",
    re.IGNORECASE,
)

MC_RE = re.compile(r"\bMC\s*[#:.]?\s*(\d{5,8})\b", re.IGNORECASE)
DOT_RE = re.compile(r"\b(?:US)?DOT\s*[#:.]?\s*(\d{5,8})\b", re.IGNORECASE)
PHONE_RE = re.compile(r"(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


def keep(text: str) -> bool:
    """LLM'ga yuborishga arziydimi?"""
    t = (text or "").strip()
    if len(t) < 8:
        return False
    if NOISE.match(t):
        return False
    return bool(SIGNAL.search(t))


def regex_hints(text: str) -> dict:
    """LLM'ga yordam beradigan va uni tekshiradigan aniq faktlar."""
    return {
        "mc_numbers": MC_RE.findall(text or ""),
        "dot_numbers": DOT_RE.findall(text or ""),
        "phones": PHONE_RE.findall(text or ""),
        "emails": EMAIL_RE.findall(text or ""),
    }
