# MC Lead Engine

FB guruhlaridan MC/DOT authority sotuvchi va sotib oluvchilarni topadi, tekshiradi,
ballaydi va bir dashboard'da ko'rsatadi.

**8 ta FB guruhi** kuzatiladi (`config.yaml`), hammasi public MC/DOT savdo guruhlari.
Hozircha faqat **matnli postlar** — comment va rasmli postlar keyingi versiyada.

**Noldan o'rnatish: [SETUP.md](SETUP.md)**

```
FB feed + comment  →  BUY/SELL/NOISE  →  FMCSA tekshiruvi  →  ball  →  match
   (Playwright)         (Groq)            (keysiz API)      (deterministik)
                                    ↓
                    localhost:8000  +  Telegram
```

## Ishga tushirish

```bash
uv pip install -e .
uv run playwright install chromium
uv run mc init
```

`.env` da to'ldirilishi kerak:

| O'zgaruvchi | Holat |
|---|---|
| `GROQ_API_KEY` | ✅ tayyor |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | ⬜ @BotFather'dan |
| `FMCSA_WEBKEY` | ❌ **kerak emas** — ishlatilmaydi |

## Buyruqlar

```bash
uv run mc login       # burner FB akkaunt bilan bir marta qo'lda login
uv run mc collect     # feed + comment yig'ish
uv run mc classify    # BUY/SELL/NOISE + faktlarni ajratish
uv run mc enrich      # MC/DOT ni FMCSA bo'yicha tekshirish
uv run mc score       # ball
uv run mc match       # buyer ↔ seller
uv run mc notify      # Telegram (--dry-run bilan sinash mumkin)
uv run mc start       # HAMMASI fonda: dvigatel + dashboard
uv run mc stop        # hammasini to'xtatadi (chromium bilan birga)

uv run mc serve       # faqat dashboard -> http://127.0.0.1:8000
uv run mc loop        # faqat dvigatel, har 10 daqiqada
uv run mc ingest-raw  # xom JSON'dan bazaga qayta yuklash (FB'ga bormasdan)
uv run mc collect --full   # chuqur skan (odatda inkremental)
uv run mc stats       # qisqacha holat
uv run mc runs        # oxirgi yangilanishlar va ular muvaffaqiyatli bo'lganmi
uv run mc reprocess   # klassifikatsiyani noldan (qayta scrape qilmasdan)
```

## Muhim texnik qarorlar

**Ingest — GraphQL tap, DOM emas.** FB feed'ni virtualizatsiya qiladi (scroll'da post
DOM'dan chiqib ketadi) va vaqt belgisini aralashtirilgan `<span>` lar bilan yashiradi.
Shuning uchun `POST /api/graphql/` javoblari ushlanadi — strukturali JSON, to'g'ri
`creation_time`, kesilmagan matn. `normalize.py` qattiq yo'l ishlatmaydi: butun JSON
daraxti bo'ylab yurib "post'ga o'xshash" tugunlarni signal bo'yicha topadi, shuning
uchun FB shaklni o'zgartirsa ham ishlashda davom etadi. DOM fallback ham bor.

**Yig'ish inkremental.** Har siklda 40 marta scroll qilish shart emas — postlarning
95% i allaqachon bazada. Ketma-ket 3 ta scroll yangi post bermasa to'xtaydi
(amalda 2–3 scroll). Bu akkaunt uchun yukni ~20 barobar kamaytiradi.
Butun tarixni qayta olish uchun `mc collect --full`.

**Xom JSON saqlanadi** (`data/raw/`). Klassifikator yoki ball o'zgarsa — `mc reprocess`,
qayta scrape kerak emas.

**FMCSA — `data.transportation.gov` (Socrata), API key yo'q.**
`mobile.fmcsa.dot.gov` (QCMobile) va `safer.fmcsa.dot.gov` ikkalasi ham 403 qaytaradi.
Socrata dataset `az4n-8mr2` esa keysiz ishlaydi va DOT/MC docket bo'yicha qidiradi.
Bu "17 yr mc" kabi da'volarni tekshiradi — yolg'on da'vo ballni tushiradi.

**Groq — `reasoning_effort: "low"` majburiy.** `gpt-oss` modellari reasoning token
sarflaydi; busiz javob `max_tokens` ichiga sig'may qoladi va Groq bo'sh javobga
`json_validate_failed` qaytaradi. `low` bilan token sarfi ~2 barobar kam.
Bu akkauntda `llama-3.3-70b` va `llama-3.1-8b` **yo'q**; mavjudlari:
`openai/gpt-oss-120b`, `openai/gpt-oss-20b`, `qwen/qwen3.6-27b`, `qwen/qwen3.8-27b`.
Limit (o'lchangan): **1000 RPD, 8000 TPM, ~200K TPD** — va bu **akkaunt boshiga**.
Shuning uchun klient bir nechta key bilan ishlaydi: `.env` da `GROQ_API_KEY`,
`GROQ_API_KEY_2`, `GROQ_API_KEY_3`… Har bir key alohida hisoblanadi (o'z daqiqalik
chelagi, o'z kunlik budjeti); biri tugasa yoki 429 bersa avtomatik keyingisiga
o'tadi. Ikkita key = ikki barobar limit.

**Talab tekshiruvi.** Sheriklarning shartlari `config.yaml` dagi `requirements` blokida:
MC minimum **6 oy**, sotuvda **bank hisobi + email + telefon** topshirilishi shart,
Amazon approved — plyus. Har bir sotuvchi lead uchta hukmdan birini oladi:
`TALABGA MOS` / `SO'RASH KERAK` (ma'lumot yetishmaydi) / `MOS EMAS`.
Yosh FMCSA'dan olinadi, da'vodan emas.

**Ball deterministik.** LLM faqat faktlarni ajratadi, ball `config.yaml` dagi
og'irliklar bilan hisoblanadi. Har bir lead'da "nega 84 ball" ochib ko'rsatiladi.

**Match bloklash qoidasi.** Comment'da "sotib olaman" degan xaridor **o'sha postdagi**
sotuvchiga hech qachon ulanmaydi — u postni allaqachon ko'rgan. Bloklanadi:
- xaridor aynan shu postda yozgan
- o'zini o'ziga
- xaridor bu postga allaqachon comment yozgan

Uning qiymati — u ko'rmagan va **kelajakdagi** sotuvchilarga ulanishida.

**Avtomatik FB DM yo'q.** Bot bilan DM yuborish akkauntni darhol o'ldiradi va spam.
Tizim tayyor intro matnini beradi (`/matches` da "nusxa" tugmasi), yuborishni siz
qilasiz.

## Dashboard

| Sahifa | Nima |
|---|---|
| `/` | KPI, Groq budjeti, tizim holati, bugungi top |
| `/sellers`, `/buyers` | Asosiy ish stoli — status tablari, filtr, saralash. `✓ Bog'landim` / `✕` tugmalari. Tashlanganlar **Tashlangan** tabidan `↺ Qaytarish` bilan tiklanadi — hech narsa o'chmaydi |
| `/matches` | Juftliklar, sabab, tayyor intro matni |
| `/people/{id}` | Odam tarixi — serial reseller va doimiy xaridorni shu yerda ko'rasiz |
| `/health` | **Yangilanishlar tarixi** — har yurish, har bosqich, xato matni bilan. Groq sarfi, FB sessiya |

## Hozirgi holat

Baza ichida **sinov ma'lumoti** bor — bu guruhda ko'zim bilan ko'rilgan 4 ta real post
va 4 ta comment (Marco Tocel, Derrick Smith, Ghansham Ramlakhan, Eric Aiken).
Ular pipeline'ni uchidan-uchiga tekshirish uchun kiritilgan.

Real yig'ishni boshlash uchun: burner FB akkaunt yaratib guruhga a'zo bo'ling, keyin
`uv run mc login`. Tozalash kerak bo'lsa: `rm data/mc.db && uv run mc init`.

## Risklar

1. **FB akkaunt bloklanishi** — burner ishlatiladi, asosiy akkaunt xavfsiz.
   Checkpoint aniqlanganda to'xtaydi va Telegram'ga xabar beradi. Bu *qachondir*
   sodir bo'ladi; yangi burner qilasiz, kod o'zgarmaydi.
2. **GraphQL shakli o'zgaradi** — xom JSON saqlanadi, DOM fallback bor, `/health` da
   ko'rinadi. Jim o'lmaydi.
3. **Groq free tier tor** — 1 haftalik backfill 1–2 kunga cho'zilishi mumkin.
   Kundalik oqimga muammo yo'q.
4. **Ma'lumot ishonchsiz** — postdagi har bir da'vo FMCSA bilan tekshiriladi.
