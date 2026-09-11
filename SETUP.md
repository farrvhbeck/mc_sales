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
sudo apt install -y xvfb             # Linux'da: brauzer oynasi ekranga chiqmasin
```

macOS va Windows'da `xvfb` yo'q — u yerda oyna boshqa yo'l bilan yashiriladi
(`mc doctor` qaysi usul ishlayotganini aytadi). Rasm o'qish uchun alohida narsa
o'rnatish shart emas: OCR modellari `uv pip install -e .` bilan keladi.

Hammasi joyidami — bitta buyruq bilan tekshiriladi:

```bash
uv run mc doctor
```

## 3. Kalitlar

```bash
cp .env.example .env
```

`.env` ni ochib to'ldiring:

**GROQ_API_KEY** — https://console.groq.com/keys → Google bilan kiring → *Create API Key*.
Bepul. Key faqat bir marta ko'rsatiladi, darhol nusxa oling.

Limit **akkaunt boshiga** (kuniga ~200K token). Kam kelsa, boshqa Google akkaunt
bilan yana bitta key olib `.env` ga `GROQ_API_KEY_2=` deb qo'shing — dastur ularni
navbat bilan ishlatadi va biri tugasa ikkinchisiga o'zi o'tadi. Nechta bo'lsa ham
bo'ladi (`GROQ_API_KEY_3`, `_4`…).

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
uv run mc ocr              # rasmli e'lonlarni o'qish
uv run mc classify
uv run mc enrich
uv run mc fraud
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

Ikkita buyruq, tamom.

**Ishga tushirish:**

```bash
uv run mc start
```

Har safar ishga tushganda ko'rinadigan Chromium oynasi ochiladi, guruhlarni
scroll qiladi va yopiladi — bu normal, dastur shunday ishlaydi.

Bu ikkalasini ham fonda ishga tushiradi — dvigatel (har 15 daqiqada yangi post
qidiradi, tahlil qiladi, Telegramga yuboradi) va dashboard. Terminalni yopsangiz
ham ishlashda davom etadi.

Brauzerda: **http://127.0.0.1:8000**

**To'xtatish:**

```bash
uv run mc stop
```

Ikkinchi marta `mc start` qilsangiz takrorlamaydi — "allaqachon ishlayapti" deydi.
Loglar: `data/loop.log` va `data/web.log`.

Laptopni yopsangiz to'xtaydi, ochsangiz `mc loop` ni qayta ishga tushirasiz.
Yo'qolgan vaqt uchun xavotir kerak emas — u oxirgi 7 kunlik postlarni ko'radi,
allaqachon ko'rilganini takrorlamaydi.

### Do'stingizga havola berish

```bash
uv run mc share
```

Cloudflare tunnel orqali internetga chiqaradi va **ishlashi tekshirilgan** havolani
beradi. Parol majburiy — parolsiz dashboard internetga chiqmaydi (parol bo'lmasa
o'zi yaratib `.env` ga yozadi).

Havolani unutgan bo'lsangiz:

```bash
uv run mc link      # hozirgi havola + parol, ishlayotgani tekshiriladi
```

Uchta cheklov, oldindan bilib qo'ying:

1. **Havola faqat shu kompyuter yoniq turganda ishlaydi.** Tunnel ham, dashboard
   ham, dvigatel ham shu yerda.
2. **Bepul tunnel manzili har safar o'zgaradi.** `mc share` qayta ishga tushirilsa
   yangi manzil beriladi. Kuzatuvchi tunnel uzilsa o'zi tiklaydi, lekin manzil
   yangisi bo'ladi — Telegram sozlangan bo'lsa yangi havolani yuboradi.
3. **Ma'lumot faqat sizda yangilanadi.** Do'stingiz ko'radi va status o'zgartiradi,
   lekin yangi leadlar siz `mc start` qilib turganingizda keladi.

Doimiy manzil kerak bo'lsa: Cloudflare akkaunt + o'z domeningiz (named tunnel),
yoki kichik VPS.

### To'xtatish haqida

`uv run mc stop` dvigatelni ham, dashboardni ham to'xtatadi va Playwright ochgan
Chromium'ni ham yopadi — yetim jarayon qolmaydi.

To'xtatish xavfsiz: yig'ilgan post, lead, status va eslatmalar bazada qoladi.
`mc start` bilan qayta boshlaganda qoldigidan davom etadi, boshidan qidirmaydi.

Faqat bittasi kerak bo'lsa:

```bash
uv run mc start --no-web      # faqat dvigatel
uv run mc start --no-engine   # faqat dashboard
uv run mc start --port 8080   # boshqa port
```

Nima bo'layotganini ko'rish:

```bash
uv run mc runs                # oxirgi yangilanishlar muvaffaqiyatli bo'lganmi
tail -f data/loop.log         # jonli log
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

**"Kunlik budjet tugadi"** — Groq bepul limiti (kuniga ~200K token har bir key
uchun). Ertaga o'zi davom etadi, yig'ish esa to'xtamaydi — xom ma'lumot saqlanadi
va `uv run mc ingest-raw` bilan keyin tahlil qilinadi. Tez-tez uchrasa `.env` ga
yana bitta key qo'shing. Qaysi key qancha sarflaganini `uv run mc stats` yoki
dashboarddagi **Holat** sahifasi ko'rsatadi.

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
