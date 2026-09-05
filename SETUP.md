# Noldan o'rnatish (laptop)

Bir martalik sozlash ~20 daqiqa. Keyin har kuni bitta buyruq.

---

## 0. Kerakli narsalar

- Linux yoki macOS laptop (Windows'da WSL2)
- **Alohida ("burner") Facebook akkaunt** — asosiy akkauntingizni ishlatmang.
  Sabab pastda, "Nega burner" bo'limida.
- Telegram akkaunt

---

## 1. Kodni olish

```bash
git clone git@github.com:farrvhbeck/mc_sales.git
cd mc_sales
```

## 2. Python muhiti

`uv` bo'lmasa:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
exec $SHELL          # PATH yangilanishi uchun
```

Keyin:

```bash
uv venv --python 3.13
uv pip install -e .
uv run playwright install chromium
uv run playwright install-deps       # Linux'da; ruxsat so'rasa parol kiriting
```

## 3. Kalitlar

```bash
cp .env.example .env
```

`.env` ni ochib to'ldiring:

**GROQ_API_KEY** — https://console.groq.com/keys → Google bilan kiring → *Create API Key*.
Bepul. Key faqat bir marta ko'rsatiladi, darhol nusxa oling.

**Telegram** — Telegram'da `@BotFather` ga yozing → `/newbot` → nom bering → token oladi.
Keyin **o'z botingizga bir marta "hi" deb yozing** (bu majburiy, aks holda bot sizga
yoza olmaydi), so'ng brauzerda oching:

```
https://api.telegram.org/bot<TOKEN>/getUpdates
```

Javobdagi `"chat":{"id":123456789}` — o'sha raqam `TELEGRAM_CHAT_ID`.

FMCSA uchun hech qanday kalit kerak emas.

## 4. Bazani yaratish

```bash
uv run mc init
```

## 5. Facebook — burner akkaunt

Burner akkaunt bilan **brauzerda odatdagidek** `MC & DOT Authorization BUY/SELL`
guruhiga a'zo bo'ling (guruh public, tez qabul qiladi). Keyin:

```bash
uv run mc login
```

Chromium ochiladi → burner akkaunt bilan kiring → terminalga qaytib **Enter** bosing.
`✅ Sessiya saqlandi` chiqishi kerak. Cookie `data/fb_profile/` da qoladi — boshqa
login kerak emas.

## 6. Birinchi yig'ish

```bash
uv run mc collect          # 5-15 daqiqa, brauzer o'zi scroll qiladi — tegmang
uv run mc classify
uv run mc enrich
uv run mc score
uv run mc match
uv run mc stats            # nima yig'ilganini ko'rish
```

Telegramni sinash (hech nima yubormaydi, faqat ko'rsatadi):

```bash
uv run mc notify --dry-run
```

---

## Kundalik ishlatish

Ikkita terminal ochiq turadi.

**Terminal 1 — dvigatel.** Har 10 daqiqada yangi post qidiradi, tahlil qiladi,
Telegramga yuboradi:

```bash
uv run mc loop
```

**Terminal 2 — dashboard:**

```bash
uv run mc serve
```

Brauzerda: **http://127.0.0.1:8000**

Laptopni yopsangiz to'xtaydi, ochsangiz `mc loop` ni qayta ishga tushirasiz.
Yo'qolgan vaqt uchun xavotir kerak emas — u oxirgi 7 kunlik postlarni ko'radi,
allaqachon ko'rilganini takrorlamaydi.

### To'xtatish

Terminal ochiq bo'lsa — **Ctrl+C**. Ikkalasi ham tinch to'xtaydi, ma'lumot yo'qolmaydi.

Terminal ko'rinmayotgan bo'lsa (tmux'da yoki fonda) — **boshqa terminaldan**:

```bash
uv run mc stop
```

Bu `mc loop` va `mc serve` ikkalasini ham to'xtatadi. Brauzer ham to'g'ri yopiladi.

To'xtatish xavfsiz — yig'ilgan post, lead, status va eslatmalar bazada qoladi.
`uv run mc loop` bilan qayta boshlaganda qoldigidan davom etadi, boshidan
qidirmaydi.

### Fonda ishlatish (terminal yopilsa ham davom etsin)

```bash
# Linux
sudo apt install tmux
tmux new -s mc
uv run mc loop
# Ctrl+B keyin D bosib chiqing.
# Qaytish:     tmux attach -t mc
# To'xtatish:  uv run mc stop
```

---

## Dashboard'dan qanday foydalanish

**Sotuvchilar** / **Xaridorlar** — asosiy ish stoli. Yuqorida status tablari:

| Tab | Ma'nosi |
|---|---|
| Yangi | Hali ko'rmagansiz. **Faqat shular Telegramga yuboriladi** |
| Bog'landim | Yozdingiz, javob kutyapsiz |
| Sifatli | Javob berdi, real odam |
| Ulandi | Xaridorga ulandi |
| Yopildi | Tugadi — sotildi yoki qo'ldan ketdi |
| Tashlangan | Spam/xato. **O'chmaydi** — `↺ Qaytarish` bilan tiklanadi |

Har qatorda: chap chetda rangli chiziq (yashil = kuchli lead), ball, ism, FMCSA nishoni,
narx, telefon. `tafsilot` ni ochsangiz to'liq matn, ballning tarkibi va FMCSA rasmiy
yozuvi chiqadi.

**FMCSA nishoni eng muhim narsa.** Yashil `✓ MC1075922 · 6.8y · FL` — bu raqam
rasmiy bazada bor, faol, va authority haqiqatan 6.8 yillik. Qizil `✗ INACTIVE` —
o'lik authority, aralashmang. Sariq `?` — raqam bazada topilmadi.
Ya'ni odam "17 yillik MC" desa, tizim buni tekshiradi.

**Match** — tizim taklif qilgan xaridor↔sotuvchi juftliklari, sababi va tayyor
inglizcha xabar bilan. "nusxa" bosib Facebook'da o'zingiz yuborasiz.

**Odam sahifasi** (ismga bosing) — o'sha odamning butun tarixi. Doim sotayotgan
qayta sotuvchini va doim qidirayotgan xaridorni shu yerda ko'rasiz.

**Yuqoridagi rangli chiziq** har sahifada turadi va oxirgi yangilanish qachon
bo'lganini aytadi:

| Rang | Ma'nosi | Nima qilish |
|---|---|---|
| Yashil "Ishlayapti" | Hammasi joyida | — |
| Ko'k "Hozir ishlayapti" | Ayni damda yig'yapti | Kuting |
| Sariq "Qisman bajarildi" | Bir bosqich tushdi, qolgani ishladi | Holat sahifasidan sababni ko'ring |
| Sariq "Yangilanmayapti" | 30 daqiqadan beri yangilik yo'q | `mc loop` to'xtagan — qayta ishga tushiring |
| Qizil "Muvaffaqiyatsiz" | Oxirgi yurish tushdi | Holat sahifasida xato matni bor |
| Qizil "Facebook sessiya tushdi" | Akkaunt bloklandi | `uv run mc login` |

**Holat** sahifasida har bir yurishning 6 bosqichi alohida ko'rinadi — qaysi biri
ishladi, qaysi biri tushdi va nima natija berdi. Ish ketmayotgandek tuyulsa
birinchi shu yerga qarang.

Terminaldan ham ko'rish mumkin:

```bash
uv run mc runs
```

---

## Nega burner akkaunt

Dastur guruhni avtomatik scroll qiladi. Facebook buni qoidalar buzilishi deb biladi
va **qachondir** akkauntga checkpoint qo'yadi — "agar" emas, "qachon". Shuning uchun:

- Asosiy akkauntingizni **hech qachon** ishlatmang
- Tushganda dastur o'zi to'xtaydi va Telegramga "sessiya tushdi" deb yozadi
- Yangi burner yaratib `uv run mc login` qilasiz — kod o'zgarmaydi, ma'lumot yo'qolmaydi

Dastur hech qachon post yozmaydi, comment qoldirmaydi, DM yubormaydi — faqat o'qiydi.
Xabar yuborishni siz qo'lda qilasiz. Bu ataylab shunday: bot bilan DM yuborish
akkauntni darhol o'ldiradi.

---

## Muammolar

**`mc login` dan keyin ham "Login tasdiqlanmadi"** — Facebook tasdiqlash so'ragan
bo'lishi mumkin. `uv run mc login` ni qayta ishga tushiring, brauzerda oxirigacha
kiring (SMS kod va h.k.), keyin Enter bosing.

**`mc collect` hech nima topmadi** — burner akkaunt guruhga a'zo emasligi ehtimoli
katta. Brauzerda tekshiring.

**Telegram jim** — `.env` da token/chat_id to'g'rimi? Botga bir marta o'zingiz
yozganmisiz? `uv run mc notify --dry-run` bilan tekshiring.

**"Kunlik budjet tugadi"** — Groq bepul limiti (kuniga ~200K token). Ertaga o'zi
davom etadi. Birinchi kuni ko'p ma'lumot bo'lgani uchun normal.

**Hammasini noldan boshlash:**

```bash
rm -rf data/mc.db && uv run mc init
```

Klassifikatsiyani qayta hisoblash (Facebook'ga qayta kirmasdan):

```bash
uv run mc reprocess && uv run mc classify
```

---

## Xavfsizlik

- `.env` va `data/` **hech qachon** git'ga tushmaydi — ichida API key va Facebook
  cookie bor. Buni hech kimga yubormang.
- Dashboard faqat `127.0.0.1` da ochiladi, internetdan ko'rinmaydi. Shunday qoldiring.
