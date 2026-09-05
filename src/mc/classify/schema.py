"""T2 ekstraksiyasi uchun JSON schema va prompt'lar."""

TRIAGE_SYSTEM = """You classify posts and comments from a Facebook group where people buy and sell US trucking MC/DOT operating authorities (and the LLCs holding them).

For each numbered item, output exactly one label:
- "SELL"  - the author is offering an MC/DOT/authority/LLC for sale, or asking how to sell theirs with clear intent to sell.
- "BUY"   - the author wants to acquire one, or says they will buy the one being discussed.
- "NOISE" - anything else: questions, advice, insurance/dispatch/factoring ads, jokes, greetings, brokers advertising services.

Rules:
- A comment saying "I'll buy it" or "DM me the price" on a sale post is BUY.
- Someone advertising dispatch, factoring, insurance, ELD, or trucking courses is NOISE.
- If unsure, choose NOISE.

Reply with JSON only, in this exact shape:
{"items": [{"i": 0, "side": "SELL"}, {"i": 1, "side": "NOISE"}]}"""

EXTRACT_SYSTEM = """You extract structured facts from a single Facebook post or comment about buying or selling a US trucking MC/DOT operating authority.

Extract only what the text actually states. Use null for anything not stated - never guess.
- Prices: convert "40k" to 40000, "$25,000" to 25000.
- state: two-letter US state code if a location is mentioned, else null.
- authority_age_years: if the text says "17 yr mc" -> 17; if it says "established since 2020" -> compute from 2026.
- contact_method: "phone", "email", "whatsapp", "dm", or null. contact_value only if an actual number/address is written.
- urgency: "high" if words like asap, urgent, today, must sell; "low" if casual; else "normal".
- confidence: 0..1, how sure you are about side and the extracted fields."""

EXTRACT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "side", "mc_number", "dot_number", "authority_since", "authority_age_years",
        "entity_type", "state", "price_usd", "price_is_negotiable", "has_amazon",
        "has_trucks", "has_insurance", "clean_record", "buyer_budget_usd",
        "buyer_wants_state", "buyer_min_age_years", "buyer_needs_amazon",
        "contact_method", "contact_value", "urgency", "confidence",
    ],
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
