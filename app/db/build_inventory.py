"""Build the cleaned inventory SQLite database from the raw car listings xlsx.

Usage: uv run python -m app.db.build_inventory

Loads data/raw/*.xlsx, cleans the scraped marketplace descriptions, best-effort
extracts price/mileage (null when ambiguous), and writes data/processed/app.db
(table: inventory) via SQLAlchemy Core.
"""

from __future__ import annotations

import html
import re
from pathlib import Path

import pandas as pd
import sqlalchemy as sa

from app.db.schema import inventory as INVENTORY

RAW_XLSX = Path("data/raw/Copy_of_sample_cars_dataset.xlsx")
DB_PATH = Path("data/processed/app.db")

# ---------------------------------------------------------------------------
# Cleaning regexes
# ---------------------------------------------------------------------------

TAG_RE = re.compile(r"<[^>]+>")
ARABIC_RE = re.compile(r"[؀-ۿ]")
URL_RE = re.compile(
    r"(?i)(?:https?://|www\.|wa\.me/)\S+|\b[\w-]+\.(?:ae|com|net|org)\b(?:/\S*)?"
)
HANDLE_RE = re.compile(r"@\w+")
HASHTAG_RE = re.compile(r"#\w+")
# UAE-style and generic international phone numbers. [ \t] only (no newlines)
# so a number can never chain across lines and eat a price/year.
PHONE_RE = re.compile(
    r"(?<!\d)(?:\+|00)\d{1,3}[\d \t\-().]{6,14}\d(?!\d)"  # +971 4 394 0068 / 0097152...
    r"|(?<!\d)971\d{8,9}(?!\d)"  # 97144501601
    r"|(?<!\d)0(?:5\d|4)[ \-]?\d{3}[ \-]?\d{4}(?!\d)"  # 050 123 4567 / 04 xxx xxxx
)

# Lines starting with these (after stripping bullets/emoji) are contact or
# marketing cruft. Deliberately best-effort — not trying to catch everything.
BOILERPLATE_PREFIXES = (
    "check out our website", "we welcome you to visit", "visit our", "visit us",
    "follow us", "stay connected", "you can also stay", "contact us", "call us",
    "call or", "for calling", "for whatsapp", "whatsapp", "instagram", "facebook",
    "linkedin", "twitter", "pinterest", "tiktok", "snapchat", "youtube",
    "speak to the team", "our location", "showroom", "open 7 days",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "office:", "sales:", "mr.", "ms.", "mob", "tel:", "phone:", "email", "e-mail",
    "website", "to find out more", "buy this car with confidence",
)

# These start a dealer-marketing paragraph; skip lines until the next blank line.
BLOCK_HEADER_PREFIXES = (
    "our services", "why choose", "who we are", "about us",
    "what is dubizzle", "what to expect", "we believe the process",
    "no guesswork", "no stress", "best part", "this service is provided",
    "need a more comprehensive", "upgrade to the advanced", "looking to finance",
)

BULLET_STRIP_RE = re.compile(r"^[\s•*\-–—_=~.·✓✔☑️✅🔹🔸▪️⭐🚗📅🔢🌍🎨​‎‏]+")
WORD_RE = re.compile(r"[\w؀-ۿ]+")
NO_CONTENT_RE = re.compile(r"^[^\w؀-ۿ]*$")


def normalize(raw: str) -> str:
    """HTML entities decoded, tags replaced with newlines."""
    return TAG_RE.sub("\n", html.unescape(raw))


def clean_description(raw: str) -> str:
    text = normalize(raw)
    out_lines: list[str] = []
    in_marketing_block = False
    for line in text.split("\n"):
        line = URL_RE.sub(" ", line)
        line = HANDLE_RE.sub(" ", line)
        line = HASHTAG_RE.sub(" ", line)
        line, n_phones = PHONE_RE.subn(" ", line)
        line = re.sub(r"[ \t]+", " ", line).strip()

        stripped = BULLET_STRIP_RE.sub("", line).lower()
        if not stripped or NO_CONTENT_RE.match(line):
            in_marketing_block = False  # blank/divider line ends a block
            continue
        if in_marketing_block:
            continue
        if stripped.startswith(BLOCK_HEADER_PREFIXES):
            in_marketing_block = True
            continue
        if stripped.startswith(BOILERPLATE_PREFIXES):
            continue
        # A line that contained a phone number and has almost nothing left is a
        # contact line ("Mr. Rasheed:", "Owen (Irish)", "FOR CALLING -").
        if n_phones and len(WORD_RE.findall(line)) <= 4:
            continue
        out_lines.append(line)
    return "\n".join(out_lines).strip()


# ---------------------------------------------------------------------------
# Structured extraction (deterministic regex, null when ambiguous)
# ---------------------------------------------------------------------------

NUM = r"(\d[\d,. ]*\d|\d)"
AED_NUM_RE = re.compile(rf"(?i)(?:aed|درهم)\s*[:\-]?\s*{NUM}|{NUM}\s*(?:aed|درهم)")
BARE_MONTHLY_RE = re.compile(rf"(?i){NUM}\s*(?:aed\s*)?(?:/\s*month|per\s+month|monthly|شهري)")
MILEAGE_LABELED_RE = re.compile(
    rf"(?i)(?:mileage|odometer|عداد(?:\s*المسافات)?|العداد)\s*(?:of\s*)?[:\-]?\s*{NUM}\s*(?:kms?|كم)?"
)
MILEAGE_STANDALONE_RE = re.compile(rf"(?i){NUM}\s?(?:kms?|كم)\b")

MONTHLY_KWS = ("month", "p/m", "شهري", "installment", "قسط")
CASH_EXCLUDE_KWS = ("down", "دفعة", "salary", "report", "deposit")
# A km figure on a line with these words is warranty/service/range, not odometer.
MILEAGE_EXCLUDE_KWS = (
    "warrant", "service", "contract", "until", "till", "guarantee",
    "range", "wltp", "charge", "km/h", "0-100", "صيانة", "ضمان",
)


def parse_amount(tok: str) -> int | None:
    tok = tok.strip().rstrip(".")
    tok = re.sub(r"\.00$", "", tok)  # 1,813.00 -> 1,813
    if re.fullmatch(r"\d{1,3}\.\d{3}", tok):  # 83.000 -> dot as thousands sep
        tok = tok.replace(".", "")
    tok = re.sub(r"[,. ]", "", tok)
    return int(tok) if tok.isdigit() else None


def extract_prices(lines: list[str]) -> tuple[int | None, int | None]:
    cash: int | None = None
    monthly: int | None = None
    for line in lines:
        low = line.lower()
        candidates = [(m, m.group(1) or m.group(2)) for m in AED_NUM_RE.finditer(line)]
        candidates += [(m, m.group(1)) for m in BARE_MONTHLY_RE.finditer(line)]
        for m, tok in candidates:
            val = parse_amount(tok)
            if val is None:
                continue
            after = low[m.end() : m.end() + 12]
            before = low[max(0, m.start() - 25) : m.start()]
            if any(k in after or k in before for k in MONTHLY_KWS):
                if monthly is None and 100 <= val <= 20_000:
                    monthly = val
            elif any(k in after or k in before for k in CASH_EXCLUDE_KWS):
                continue  # down payment / salary / report fee — not a car price
            elif cash is None and val >= 10_000:
                cash = val
    return cash, monthly


def extract_mileage(lines: list[str]) -> int | None:
    labeled: list[int] = []
    standalone: list[int] = []
    excluded_vals: set[int] = set()
    for line in lines:
        low = line.lower()
        line_excluded = any(k in low for k in MILEAGE_EXCLUDE_KWS)
        for m in MILEAGE_LABELED_RE.finditer(line):
            val = parse_amount(m.group(1))
            if val is not None and not line_excluded:
                labeled.append(val)
        for m in MILEAGE_STANDALONE_RE.finditer(line):
            val = parse_amount(m.group(1))
            if val is None:
                continue
            (excluded_vals.add if line_excluded else standalone.append)(val)
    # A value that also appears in an excluded context (e.g. "701KM range"
    # elsewhere in the ad) is too ambiguous to trust.
    standalone = [v for v in standalone if v not in excluded_vals]
    for val in labeled + standalone:
        if 0 <= val <= 500_000:
            return val
    return None


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def build_dataframe() -> pd.DataFrame:
    df = pd.read_excel(RAW_XLSX)
    df.insert(0, "id", range(1, len(df) + 1))  # stable 1-indexed id
    df["description_clean"] = df["description"].map(clean_description)
    df["is_arabic"] = df["description"].map(lambda d: bool(ARABIC_RE.search(d)))

    cash_vals, monthly_vals, mileage_vals = [], [], []
    for _, row in df.iterrows():
        # Extract from title + description: prices/mileage often live in either.
        lines = [row["title"], *normalize(row["description"]).split("\n")]
        cash, monthly = extract_prices(lines)
        cash_vals.append(cash)
        monthly_vals.append(monthly)
        mileage_vals.append(extract_mileage(lines))
    df["price_aed_cash"] = pd.array(cash_vals, dtype="Int64")
    df["price_aed_monthly"] = pd.array(monthly_vals, dtype="Int64")
    df["mileage_km"] = pd.array(mileage_vals, dtype="Int64")
    return df


def write_db(df: pd.DataFrame) -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    engine = sa.create_engine(f"sqlite:///{DB_PATH}")
    # Only this table — the shared metadata also holds users/sessions/etc.
    INVENTORY.drop(engine, checkfirst=True)
    INVENTORY.create(engine)
    records = df[[c.name for c in INVENTORY.columns]].to_dict("records")
    records = [
        {k: (None if pd.isna(v) else v) for k, v in rec.items()} for rec in records
    ]
    with engine.begin() as conn:
        conn.execute(INVENTORY.insert(), records)


def main() -> None:
    df = build_dataframe()
    write_db(df)

    n = len(df)
    print(f"Wrote {n} rows to {DB_PATH} (table: inventory)")
    print(f"  price_aed_cash populated:    {df['price_aed_cash'].notna().sum()}/{n}")
    print(f"  price_aed_monthly populated: {df['price_aed_monthly'].notna().sum()}/{n}")
    print(f"  mileage_km populated:        {df['mileage_km'].notna().sum()}/{n}")
    print(f"  is_arabic flagged:           {df['is_arabic'].sum()}/{n}")

    for idx in (0, 5, 24):  # boilerplate-heavy, dubizzle block, Arabic
        row = df.iloc[idx]
        print("\n" + "=" * 78)
        print(f"id={row['id']} | {row['title']}")
        print(
            f"cash={row['price_aed_cash']} monthly={row['price_aed_monthly']} "
            f"mileage={row['mileage_km']} arabic={row['is_arabic']}"
        )
        print("-" * 30, "RAW", "-" * 30)
        print(row["description"])
        print("-" * 30, "CLEAN", "-" * 28)
        print(row["description_clean"])


if __name__ == "__main__":
    main()
