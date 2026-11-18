"""Countries, default cities and niche presets."""
from __future__ import annotations

COUNTRIES: dict[str, dict] = {
    "US": {"name": "United States", "gl": "us", "cities": [
        "New York", "Los Angeles", "Chicago", "Houston", "Phoenix", "Philadelphia", "San Antonio",
        "San Diego", "Dallas", "Austin", "Miami", "Atlanta", "Seattle", "Denver", "Boston"]},
    "GB": {"name": "United Kingdom", "gl": "uk", "cities": [
        "London", "Manchester", "Birmingham", "Leeds", "Glasgow", "Liverpool", "Bristol",
        "Edinburgh", "Sheffield", "Leicester"]},
    "AE": {"name": "United Arab Emirates", "gl": "ae", "cities": [
        "Dubai", "Abu Dhabi", "Sharjah", "Ajman", "Ras Al Khaimah"]},
    "CA": {"name": "Canada", "gl": "ca", "cities": [
        "Toronto", "Vancouver", "Montreal", "Calgary", "Ottawa", "Edmonton"]},
    "AU": {"name": "Australia", "gl": "au", "cities": [
        "Sydney", "Melbourne", "Brisbane", "Perth", "Adelaide"]},
    "SA": {"name": "Saudi Arabia", "gl": "sa", "cities": ["Riyadh", "Jeddah", "Dammam"]},
}

ALIASES = {"USA": "US", "UK": "GB", "UAE": "AE", "KSA": "SA"}


def normalize_country(code: str) -> str:
    c = code.strip().upper()
    c = ALIASES.get(c, c)
    if c not in COUNTRIES:
        raise ValueError(f"Unknown country '{code}'. Known: {', '.join(COUNTRIES)} (aliases: USA, UK, UAE, KSA). "
                         f"Add new countries in the dashboard under Locations.")
    return c


def country_name(code: str) -> str:
    return COUNTRIES.get(code, {}).get("name", code)


def default_cities(code: str, n: int | None = None) -> list[str]:
    cities = COUNTRIES[code]["cities"]
    return cities[:n] if n else cities


# Which niches tend to buy which service — used by `leadforge niches`
# and to suggest targets when the user only picks a service.
SERVICE_NICHES: dict[str, list[str]] = {
    "seo": ["dentist", "lawyer", "plumber", "hvac", "roofer", "med spa", "real estate agent",
            "chiropractor", "personal injury lawyer", "electrician"],
    "ads": ["cosmetic clinic", "home services", "gym", "restaurant", "car dealer",
            "immigration consultant", "moving company", "solar installer"],
    "web": ["contractor", "salon", "restaurant", "cleaning service", "auto repair",
            "landscaper", "bakery", "tailor"],
    "chatbot": ["dental clinic", "real estate agency", "hotel", "law firm", "coaching institute",
                "medical clinic", "car rental", "travel agency"],
}

# --expand: one niche → several search phrasings. Google Maps caps each query at ~60
# results, so related phrasings surface different businesses (dedupe merges overlaps).
NICHE_SYNONYMS: dict[str, list[str]] = {
    "dentist": ["dental clinic", "cosmetic dentist", "orthodontist", "emergency dentist"],
    "lawyer": ["attorney", "law firm", "personal injury lawyer", "family lawyer"],
    "plumber": ["plumbing company", "emergency plumber", "drain cleaning"],
    "hvac": ["air conditioning repair", "heating contractor", "ac repair"],
    "roofer": ["roofing contractor", "roof repair"],
    "real estate agent": ["real estate agency", "realtor", "property agent"],
    "restaurant": ["cafe", "takeaway", "fine dining"],
    "salon": ["hair salon", "beauty salon", "nail salon", "barber shop"],
    "gym": ["fitness center", "personal trainer", "yoga studio"],
    "med spa": ["medical spa", "aesthetic clinic", "botox clinic"],
    "clinic": ["medical clinic", "polyclinic", "doctor"],
    "contractor": ["general contractor", "home remodeling", "construction company"],
    "cleaning service": ["house cleaning", "commercial cleaning", "maid service"],
    "auto repair": ["car repair", "mechanic", "car service center"],
    "chiropractor": ["chiropractic clinic", "physiotherapist"],
}


def expand_niche(niche: str) -> list[str]:
    n = niche.strip().lower()
    return [n] + [s for s in NICHE_SYNONYMS.get(n, []) if s != n]


SERVICES = {
    "seo": "SEO",
    "ads": "Google / Meta Ads",
    "web": "Website Development",
    "chatbot": "AI Chatbot / Agents",
}

# Filled from the catalog (catalog.apply): service id -> pitch hook template
PITCH_HOOKS: dict[str, str] = {}

# NOTE: the dicts above are only the *seed* defaults. At runtime catalog.apply() replaces
# their contents with the user's editable catalog (dashboard → Services / Niches / Locations).
# Entry points (CLI main, web app startup, run_pipeline) call catalog.apply() first.
