"""T0 -- bepul regex filtri. Shovqinning katta qismini LLM'gacha o'ldiradi."""

from __future__ import annotations

import re

# Sotish/olish niyatini bildiruvchi signal.
# Ispancha so'zlar 2026-09-11 dagi `mc audit` dan keyin qo'shildi: guruhda
# lotin amerikasidan kelgan e'lonlar bor, faqat inglizcha regex ularni tashlab
# yuborardi ("Vendo mi LLC y MC de Alabama").
SIGNAL = re.compile(
    r"\b(sell|selling|sale|sold)\b"
    r"|\b(buy|buying|buyer|purchase|purchasing|interested)\b"
    r"|\blooking\s+for\b|\bneed\b|\bwant(ed)?\b"
    r"|\b(vendo|venta|vender|compro|comprar|busco|se\s+vende)\b"
    r"|\b(mc|dot|authority|llc)\b\s*[#:]?\s*\d{5,}"
    r"|\bmc\s*(number|#)|\bdot\s*(number|#)"
    r"|\bauthority\b"
    r"|\$\s*\d|\b\d+\s*k\b"
    r"|\bdm\b|\binbox\b|\bpm\s+me\b",
    re.IGNORECASE,
)

# Ikkinchi qoida: kalit so'z yo'q, lekin mavzu + savdo konteksti bor.
# "4 years old MC with highway setup. Clean record." -- yuqoridagi ro'yxatdagi
# birorta so'z yo'q, lekin bu aniq e'lon. Shuning uchun MAVZU va KONTEKST
# birga kelganda ham o'tkazamiz -- ikkalasi shart, aks holda shovqin kiradi.
DOMAIN = re.compile(
    r"\b(mc|dot|usdot|authority|llc|corp|inc|carrier|trucking|company|compania|compañia)\b",
    re.IGNORECASE,
)
CONTEXT = re.compile(
    r"\b\d+\s*(year|yr|yrs|month|mo|mos)s?\b"          # "4 years old", "8 month"
    r"|\b(aged?|old)\b"
    r"|\bhow\s+much\b|\bcuanto\b|\bprice\b|\bprecio\b|\bcost\b"
    r"|\bamazon\b|\bclean\s+record\b|\bmotus\b|\bhighway\s+setup\b"
    r"|\bbank\s+account\b|\bfmcsa\b|\bactive\b",
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
    """LLM'ga yuborishga arziydimi?

    Bu filtr **jim eskiradi**, shuning uchun `mc audit` uni muntazam o'lchaydi:
    tashlanganlardan namuna olib LLM'ga yuboradi va nechtasi aslida lead
    bo'lganini aytadi.
    """
    t = (text or "").strip()
    if len(t) < 8:
        return False
    if NOISE.match(t):
        return False
    if SIGNAL.search(t):
        return True
    return bool(DOMAIN.search(t) and CONTEXT.search(t))


def regex_hints(text: str) -> dict:
    """LLM'ga yordam beradigan va uni tekshiradigan aniq faktlar."""
    return {
        "mc_numbers": MC_RE.findall(text or ""),
        "dot_numbers": DOT_RE.findall(text or ""),
        "phones": PHONE_RE.findall(text or ""),
        "emails": EMAIL_RE.findall(text or ""),
    }
