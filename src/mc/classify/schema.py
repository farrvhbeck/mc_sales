"""T2 ekstraksiyasi uchun JSON schema va prompt'lar."""

TRIAGE_SYSTEM = """You classify posts and comments from a Facebook group where people buy and sell US trucking MC/DOT operating authorities (and the LLCs holding them).

For each numbered item, output exactly one label:
- "SELL"  - the author is offering an MC/DOT/authority/LLC for sale, or asking how to sell theirs with clear intent to sell.
- "BUY"   - the author wants to acquire one, or says they will buy the one being discussed.
- "NOISE" - anything else: questions, advice, insurance/dispatch/factoring ads, jokes, greetings, brokers advertising services.

Rules:
- A comment saying "I'll buy it" or "DM me the price" on a sale post is BUY.
- Someone advertising dispatch, factoring, insurance, ELD, or trucking courses is NOISE.
- Decide by who ENDS UP OWNING the authority, not by which verb appears.
  Worked examples:
    "Selling my 2 year old MC, DM me"            -> SELL (author gives it up)
    "if someone wants to sell their mc text me"  -> BUY  (author receives it)
    "Anyone selling an MC? I pay cash"           -> BUY
    "I have an MC for sale"                      -> SELL
    "Looking for MC to purchase"                 -> BUY
  The word "sell" appears in BUY posts all the time - read the direction.
- LEASING is not selling. "Looking to lease my MC", "lease my authority to drivers",
  "run under my authority" -> NOISE. Only permanent transfer of the MC/DOT/LLC counts.
- Brokers or drivers looking for an authority to work UNDER are NOISE, not BUY.
- "Help you set up a brand new MC" / authority filing services -> NOISE.
- If unsure, choose NOISE.
- Text after "[image text]" was read from a picture by OCR and may be garbled; judge
  it by what it clearly says, not by the noise around it.

Reply with JSON only, in this exact shape:
{"items": [{"i": 0, "side": "SELL"}, {"i": 1, "side": "NOISE"}]}"""

EXTRACT_SYSTEM = """You extract structured facts from a single Facebook post or comment about buying or selling a US trucking MC/DOT operating authority.

Extract only what the text actually states. Use null for anything not stated - never guess.
- Prices: convert "40k" to 40000, "$25,000" to 25000.
- state: two-letter US state code if a location is mentioned, else null.
- authority_age_years: if the text says "17 yr mc" -> 17; if it says "established since 2020" -> compute from 2026.
- contact_method: "phone", "email", "whatsapp", "dm", or null. contact_value only if an actual number/address is written.
- urgency: "high" if words like asap, urgent, today, must sell; "low" if casual; else "normal".
- confidence: 0..1, how sure you are about side and the extracted fields.

Buyers of these entities need the whole shell handed over, so pay close attention to:
- includes_bank / includes_email / includes_phone: true ONLY if the text says the business
  bank account / email / phone number comes with the sale ("comes with bank account",
  "email and phone included", "full package", "everything included"). false if the text
  says they are NOT included. null if not mentioned at all - this is the common case.
- amazon_status: "approved" (has an active/clean Amazon Relay account), "rejected"
  (applied and was denied - "Amazon Rejected"), "never_applied" ("never applied to Amazon",
  which buyers often prefer because they can apply fresh), or null if not mentioned.
- authority_age_years: convert months to years ("8-Month-Old MC" -> 0.67,
  "at least 1 yr aged" -> 1.0, "2y+" -> 2.0).

A section marked "[image text]" was read out of a picture by OCR, so it may contain
misread characters - especially in numbers. Use it, but if the same fact appears in
both the typed text and the image text, trust the typed text. If a number in the
image text looks malformed (wrong digit count for an MC/DOT, impossible price),
return null rather than guessing."""

EXTRACT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "side": {"type": "string", "enum": ["SELL", "BUY", "NOISE"]},
        "mc_number": {"type": ["string", "null"]},
        "dot_number": {"type": ["string", "null"]},
        "authority_since": {"type": ["string", "null"], "description": "year or date as written"},
        "authority_age_years": {"type": ["number", "null"]},
        "entity_type": {"type": ["string", "null"], "enum": ["mc", "dot", "mc+dot", "llc", None]},
        "state": {"type": ["string", "null"]},
        "price_usd": {"type": ["integer", "null"]},
        "price_is_negotiable": {"type": ["boolean", "null"]},
        "has_amazon": {"type": ["boolean", "null"]},
        "amazon_status": {"type": ["string", "null"],
                          "enum": ["approved", "rejected", "never_applied", None]},
        "includes_bank": {"type": ["boolean", "null"]},
        "includes_email": {"type": ["boolean", "null"]},
        "includes_phone": {"type": ["boolean", "null"]},
        "has_trucks": {"type": ["boolean", "null"]},
        "has_insurance": {"type": ["boolean", "null"]},
        "clean_record": {"type": ["boolean", "null"]},
        "buyer_budget_usd": {"type": ["integer", "null"]},
        "buyer_wants_state": {"type": ["string", "null"]},
        "buyer_min_age_years": {"type": ["number", "null"]},
        "buyer_needs_amazon": {"type": ["boolean", "null"]},
        "contact_method": {"type": ["string", "null"]},
        "contact_value": {"type": ["string", "null"]},
        "urgency": {"type": ["string", "null"]},
        "confidence": {"type": "number"},
    },
}

# Groq strict rejimi `required` da HAR BIR property bo'lishini talab qiladi --
# qo'lda yozilsa maydon qo'shganda unutiladi, shuning uchun generatsiya qilamiz.
EXTRACT_SCHEMA["required"] = list(EXTRACT_SCHEMA["properties"])
