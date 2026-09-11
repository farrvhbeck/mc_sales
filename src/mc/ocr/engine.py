"""Rasmdan matn o'qish -- RapidOCR (PP-OCR modellari, ONNX, CPU).

Nega lokal OCR: bu Groq akkauntida vision model yo'q (o'lchangan 2026-09-11 --
gpt-oss, qwen3, whisper; vision yo'q). RapidOCR kalit va kvota talab qilmaydi,
CPU'da ~2-3s/rasm, va eng muhimi **har bir qator uchun ishonch balli** qaytaradi.
Aynan shu ball "rasm topga chiqadimi yoki qo'lda ko'riladimi" degan qarorni beradi.

Model fayllari paket ichida keladi -- internet kerak emas.
"""

from __future__ import annotations

import re
from pathlib import Path

_engine = None

# Rasmni juda kichik bo'lsa kattalashtiramiz -- telefon skrinshotlarida
# matn mayda bo'lib, detektor uni umuman ko'rmaydi.
MIN_WIDTH = 800


def available() -> bool:
    try:
        import rapidocr_onnxruntime  # noqa: F401
        return True
    except Exception:
        return False


def _get():
    global _engine
    if _engine is None:
        from rapidocr_onnxruntime import RapidOCR

        _engine = RapidOCR()
    return _engine


def _prepare(path: Path):
    """Kerak bo'lsa kattalashtirilgan rasm yo'li yoki massivini qaytaradi."""
    try:
        import cv2

        img = cv2.imread(str(path))
        if img is None:
            return str(path)
        h, w = img.shape[:2]
        if w < MIN_WIDTH:
            k = MIN_WIDTH / w
            img = cv2.resize(img, (int(w * k), int(h * k)), interpolation=cv2.INTER_CUBIC)
        return img
    except Exception:
        return str(path)


def _lines_to_text(lines: list[dict]) -> str:
    """Qatorlarni yuqoridan pastga, chapdan o'ngga tiklaydi.

    Flayerda tartib ma'noni o'zgartiradi ("MC 2 yr" va "$18,000" bir-biriga
    tegishli), shuning uchun detektor bergan tartibga ishonmaymiz.
    """
    if not lines:
        return ""
    heights = sorted((l["y1"] - l["y0"]) for l in lines)
    row_tol = max(12.0, heights[len(heights) // 2] * 0.6)

    rows: list[list[dict]] = []
    for line in sorted(lines, key=lambda l: l["y0"]):
        if rows and abs(line["y0"] - rows[-1][0]["y0"]) <= row_tol:
            rows[-1].append(line)
        else:
            rows.append([line])

    out = []
    for row in rows:
        row.sort(key=lambda l: l["x0"])
        out.append("  ".join(l["text"] for l in row))
    return "\n".join(out)


def _confidence(lines: list[dict]) -> float:
    """Hujjat darajasidagi ishonch -- qator uzunligiga tortilgan o'rtacha.

    Oddiy o'rtacha yaramaydi: bitta "MC" degan qisqa qator 0.99 bilan o'qilsa,
    u butun rasmni ishonchli ko'rsatib qo'yadi.
    """
    total = sum(max(1, len(l["text"])) for l in lines)
    if not total:
        return 0.0
    return sum(l["score"] * max(1, len(l["text"])) for l in lines) / total


def read(path: str | Path) -> dict:
    """{"text", "conf", "lines", "engine"} -- rasmdan o'qilgan matn."""
    path = Path(path)
    raw, _elapse = _get()(_prepare(path))

    lines = []
    for item in raw or []:
        box, text, score = item[0], item[1], float(item[2])
        text = (text or "").strip()
        if not text:
            continue
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        lines.append({"text": text, "score": score,
                      "x0": min(xs), "y0": min(ys), "y1": max(ys)})

    return {
        "text": _lines_to_text(lines),
        "conf": round(_confidence(lines), 4),
        "lines": [{"text": l["text"], "score": round(l["score"], 4)} for l in lines],
        "engine": "rapidocr",
    }


# OCR "1" va "l", "0" va "O" ni aralashtiradi. Raqam kutilayotgan joyda buni
# tuzatamiz -- MC/DOT/telefon regexlari shundagina ishlaydi.
_CONFUSE = str.maketrans({"O": "0", "o": "0", "l": "1", "I": "1", "|": "1", "S": "5"})


def digits_fix(text: str) -> str:
    """Raqamli bo'laklardagi harflarni raqamga qaytaradi.

    Faqat ko'pchiligi raqam bo'lgan tokenlarga tegadi, shuning uchun oddiy
    so'zlar buzilmaydi.
    """
    def fix(m: re.Match) -> str:
        tok = m.group(0)
        n_digit = sum(ch.isdigit() for ch in tok)
        alpha = [ch for ch in tok if ch.isalpha()]
        # Faqat raqam kutilayotgan token: ko'p raqam bor va undagi harflarning
        # HAMMASI adashtiriladiganlardan. "MC1075922" tegilmaydi -- M va C
        # ro'yxatda yo'q, demak bu raqam emas, prefiks.
        if n_digit >= 2 and len(tok) >= 4 and alpha and all(ch in "OolI|S" for ch in alpha):
            return tok.translate(_CONFUSE)
        return tok

    return re.sub(r"\S+", fix, text)
