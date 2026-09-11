# MC Lead Engine

FB guruhlaridan MC/DOT authority sotuvchi va sotib oluvchilarni topadi, tekshiradi,
ballaydi va bir dashboard'da ko'rsatadi.

**8 ta FB guruhi** kuzatiladi, hammasi public MC/DOT savdo guruhlari. Guruhlar
dashboard'ning `/groups` sahifasidan boshqariladi — havola tashlab qo'shasiz.
Ixtiyoriy **keng qidiruv** butun ochiq FB bo'ylab qidiradi (a'zo bo'lmagan
guruhlardagi e'lonlar ham chiqadi).
Matnli va **rasmli** postlar — flayer ichidagi matn OCR bilan o'qiladi.
Comment yig'ish kodda bor, lekin hozircha o'chirilgan.

**Noldan o'rnatish: [SETUP.md](SETUP.md)**

```
FB feed  →  rasm OCR  →  BUY/SELL/NOISE  →  FMCSA  →  firibgarlik  →  ball  →  match
(Playwright) (RapidOCR)     (Groq)        (keysiz)    (o'z bazamiz)  (deterministik)
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
uv run mc doctor      # muhit tayyormi: OCR, brauzer, sessiya, kalitlar
uv run mc login       # burner FB akkaunt bilan bir marta qo'lda login
uv run mc groups      # manbalar ro'yxati; --add <havola> bilan yangisi
uv run mc collect     # feed + rasm yig'ish
uv run mc ocr         # rasmlardagi matnni o'qish
uv run mc classify    # BUY/SELL/NOISE + faktlarni ajratish
uv run mc enrich      # MC/DOT ni FMCSA bo'yicha tekshirish
uv run mc fraud       # bitta telefon ortidagi ko'p MC, bitta MC ortidagi ko'p odam
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
uv run mc reindex     # qidiruv indeksini qayta qurish
uv run mc audit       # T0 filtri nechta haqiqiy leadni tashlayotganini o'lchash
uv run mc enrich --recheck-only   # eskirgan leadlarni FMCSA'da qayta so'rash
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

**Rasmli postlar — lokal OCR.** E'lonlarning bir qismi flayer: matnda "DM me",
hamma fakt esa rasm ichida. Groq'da vision model yo'q (o'lchangan: gpt-oss, qwen3,
whisper), shuning uchun OCR lokal — **RapidOCR** (PP-OCR modellari ONNX'da, CPU'da
~2s/rasm, kalit va kvota kerak emas). U har bir qator uchun ishonch balli beradi,
qaror shunga qarab chiqadi:

- ishonch ≥ `ocr.min_conf` va matn sotuv/xaridga o'xshasa → odatdagi LLM kaskadiga
  tushadi va ballga qarab ro'yxat tepasiga chiqadi;
- aks holda `/images` sahifasining "Needs review" tabida qoladi — rasm odamga
  ko'rsatiladi, u matnni tuzatib bir bosishda navbatga qaytaradi.

Rasm **yig'ish paytida** diskka olinadi (`data/media/`): FB CDN havolasi imzolangan
va bir necha kunda o'ladi. Rasm har doim lead kartasida ko'rinadi — OCR 98% ishonch
bilan o'qiganda ham, chunki raqamni ko'z bilan tekshirish arzon. OCR MC raqamini
xato o'qisa, FMCSA tekshiruvi buni o'zi tutadi.

**Qidiruv — SQLite FTS5.** bm25 tartibi, rasm matni ham indeksda. Raqamlar alohida
ustunda va normalizatsiya qilinadi, shuning uchun `(305) 555-0101`, `305-555-0101`
va `3055550101` bir xil natija beradi. Indeks har `score` dan keyin qayta quriladi.

**Firibgarlik grafigi.** FB profilidan ko'rinmaydigan, lekin bizning bazamizda
ko'rinadigan ikki narsa: bitta telefon ostida bir nechta har xil MC, va bitta MC
raqami ikki xil akkauntdan. Ikkalasi ham ballga tushadi va sababi ro'yxatda ochiq
yoziladi (`config.yaml` → `fraud`).

**Brauzer oynasi ko'rinmaydi.** `collect.window: auto` — Linux'da Xvfb virtual
ekrani (brauzer haqiqiy headful bo'lib qoladi, faqat ekranga chiqmaydi; FB uchun
headless'dan xavfsizroq), macOS'da oyna yashiriladi, Windows'da headless.
Qaysi usul ishlayotganini `mc doctor` aytadi.

**Manbalar — bazada, kodda emas.** `config.yaml` dagi guruhlar ro'yxati endi faqat
boshlang'ich qiymat: baza bo'sh bo'lsa bir marta ko'chiriladi. Keyin guruh qo'shish
`/groups` sahifasidan, havola tashlab bo'ladi. Raqamli havola darhol ishlaydi, nomli
havola (`/groups/mcforsale/`) keyingi yig'ishda brauzer orqali aniqlanadi. Guruhni
o'chirmasdan vaqtincha to'xtatish mumkin — yig'ilgan postlar joyida qoladi.
Burner akkaunt guruhga **a'zo bo'lishi shart** — bot o'zi a'zo bo'lmaydi (bu ban
yo'li); a'zo bo'lmasa guruh `no access` deb belgilanadi.

**Keng qidiruv.** Guruh feed'i faqat a'zo bo'lgan joyni ko'radi. `/search/posts/?q=`
esa butun ochiq FB ni. Bu eng arzon "ko'proq lead" manbai — yangi guruhga a'zo
bo'lish, ya'ni qo'shimcha ban riski talab qilmaydi. `/groups` dagi tugmadan yoqiladi,
so'rovlar o'sha yerda tahrirlanadi. Bunday postlar `Broad search` manbasi bilan
belgilanadi.

**Eskirgan leadlar qayta tekshiriladi.** Sotuvchi e'lon qo'yganidan keyin authority
o'lishi yoki sotilib ketishi mumkin. 2 haftadan oshgan sotuv leadlari FMCSA'da
qaytadan so'raladi; status o'zgarsa lead kartasida qizil chiziq chiqadi va Telegram'ga
xabar ketadi. Bu "allaqachon o'lgan MC ni xaridorga taklif qilish" xatosini oldini oladi.

**T0 filtri o'lchanadi.** Regex postlarning katta qismini LLM'gacha o'ldiradi — arzon,
lekin **jim eskiradi**: guruhda til o'zgaradi, regex esa qolib ketadi. `mc audit`
tashlanganlardan namuna olib LLM'ga yuboradi va "50 tadan 3 tasi aslida lead edi"
deb aytadi, topilganlarini navbatga qaytaradi. Natija `/health` da ko'rinadi.
Birinchi audit 13.6% yo'qotish ko'rsatdi — sababi ispancha e'lonlar
("Vendo mi LLC y MC") va kalit so'zsiz yozilgan postlar edi; filtr shunga qarab
kengaytirildi, endi 5.3%.

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
| `/groups` | **Manbalar** — guruhlar ro'yxati, havola bo'yicha qo'shish, vaqtincha to'xtatish, keng qidiruv tugmasi va so'rovlari |
| `/images` | Rasmli postlar. **Needs review** — OCR ishonchsiz o'qiganlari: rasmni ko'rib matnni tuzatasiz, bir bosishda lead bo'ladi |
| `/people/{id}` | Odam tarixi — serial reseller va doimiy xaridorni shu yerda ko'rasiz |
| `/health` | **Yangilanishlar tarixi** — har yurish, har bosqich, xato matni bilan. Groq sarfi, FB sessiya |

## Sheriklarga

Yig'uvchi **bitta** kompyuterda ishlaydi. Sheriklarga hech narsa o'rnatish shart
emas — ular `uv run mc share` bergan havolani va parolni oladi. Ikkinchi collector
= ikkinchi burner FB akkaunt = ikki barobar ban riski, foydasi esa yo'q (baza bitta).

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
