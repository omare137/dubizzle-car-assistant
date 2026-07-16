"""Tests for the orchestrator's reply-mention card filter — no LLM calls.

Cards must only render cars the reply actually references, in the order the
reply names them (the API contract's stated semantics for `cars`). Reproduces
two live bugs: a price-capped SUV search returned an unlisted-price Ferrari
the reply never mentioned yet its card rendered; and a generic "Toyota SUVs"
phrase dragged every searched Toyota into the cards alongside the three
actually recommended.
"""

from app.agent.orchestrator import _cars_mentioned_in_reply


def car(id_: int, make: str, model: str, title: str = "") -> dict:
    return {"id": id_, "make": make, "model": model, "title": title}


# The actual reply from the live bug report (abridged, car names verbatim).
FERRARI_BUG_REPLY = """\
I have one SUV under 150,000 AED with a listed price:

* 2026 Haval H9 4WD: listed at 115,750 AED.
I also have several other SUVs that might fit your budget, but their prices are not listed:

* 2026 Mercedes-Benz GLC 200 Coupe
* 2024 Ford Territory Titanium
* 2020 Mercedes-Benz GLC 300 4Matic
* 2024 Mazda CX-5
Would you like to know more about the Haval H9?"""


def test_ferrari_never_mentioned_is_dropped():
    cars = {
        1: car(1, "ferrari", "f430"),
        5: car(5, "haval", "h9"),
        2: car(2, "mercedes-benz", "glc-class"),
        3: car(3, "ford", "territory"),
        9: car(9, "mazda", "cx-5"),
    }
    kept = _cars_mentioned_in_reply(cars, FERRARI_BUG_REPLY, booking_prompt=None)
    assert {c["id"] for c in kept} == {5, 2, 3, 9}  # everything named; no Ferrari


TOYOTA_BUG_REPLY = """\
I've taken a look at our current inventory for Toyota SUVs under 150,000 AED:

*   **2025 Toyota Urban Cruiser GLX:** priced at 69,900 AED.
*   **2023 Toyota Prado GXR:** priced at 150,000 AED.

There is also a 2025 Toyota BZ4X (all-electric SUV), but its price isn't listed."""


def test_generic_make_mention_does_not_drag_in_unnamed_models():
    cars = {
        8: car(8, "toyota", "yaris"),
        29: car(29, "toyota", "hilux"),
        31: car(31, "toyota", "bz4x"),
        50: car(50, "toyota", "land cruiser 70 series"),
        67: car(67, "toyota", "prado"),
        86: car(86, "toyota", "urban cruiser"),
    }
    kept = _cars_mentioned_in_reply(cars, TOYOTA_BUG_REPLY, None)
    # Only the three named models — and 'Urban Cruiser' must not claim the
    # Land Cruiser 70 Series via the shared 'cruiser' token.
    assert {c["id"] for c in kept} == {31, 67, 86}


def test_make_fallback_when_reply_names_no_models():
    cars = {59: car(59, "bmw", "m2"), 79: car(79, "bmw", "i4")}
    kept = _cars_mentioned_in_reply(cars, "I found a few BMWs under 200k — see below.", None)
    assert {c["id"] for c in kept} == {59, 79}


def test_hyphenated_model_matches_partial_name():
    cars = {2: car(2, "mercedes-benz", "glc-class")}
    kept = _cars_mentioned_in_reply(cars, "The GLC 200 Coupe is a great option.", None)
    assert [c["id"] for c in kept] == [2]


def test_collapsed_model_match_for_generic_token_models():
    cars = {6: car(6, "mercedes-benz", "c-class")}
    kept = _cars_mentioned_in_reply(cars, "Here's more on the C-Class you asked about.", None)
    assert [c["id"] for c in kept] == [6]


def test_generic_tokens_do_not_match():
    cars = {6: car(6, "mercedes-benz", "c-class")}
    reply = "We offer first-class service on every new car."
    assert _cars_mentioned_in_reply(cars, reply, None) == []


def test_booking_prompt_car_always_kept():
    cars = {7: car(7, "audi", "q8"), 8: car(8, "kia", "sorento")}
    reply = "Would Wednesday at 14:00 work for you to come see it?"
    kept = _cars_mentioned_in_reply(cars, reply, {"car_id": 7, "day": "Wednesday", "time": "14:00"})
    assert [c["id"] for c in kept] == [7]


def test_no_mentions_returns_empty():
    cars = {1: car(1, "ferrari", "f430")}
    assert _cars_mentioned_in_reply(cars, "Nothing matches your criteria, sorry.", None) == []


def test_empty_cars_ok():
    assert _cars_mentioned_in_reply({}, "any reply", None) == []


def test_cards_follow_reply_mention_order():
    cars = {  # deliberately NOT in reply order
        3: car(3, "ford", "territory"),
        5: car(5, "haval", "h9"),
    }
    kept = _cars_mentioned_in_reply(cars, "The Haval H9 and Ford Territory both fit.", None)
    assert [c["id"] for c in kept] == [5, 3]
    kept = _cars_mentioned_in_reply(cars, "The Ford Territory and Haval H9 both fit.", None)
    assert [c["id"] for c in kept] == [3, 5]


# The live example this ordering change came from: the reply presents the
# cars top to bottom; the cards must render in that same order. The 330i and
# V220d are stored under family model names, so their variant codes only
# appear in the listing titles.
LUXURY_REPLY = """\
We have several luxury vehicles in our inventory:

* 2023 Audi Q8 (55 TFSI Quattro S Line): 204,999 AED.
* 2021 Land Rover Range Rover Vogue P525 Autobiography: 224,999 AED.
* 2022 BMW M4 Competition X-Drive: 234,999 AED. A convertible.
* 2025 BMW 330i M Sport: 225,000 AED. A near-new sedan.
* 2024 Mercedes-Benz V220d 4MATIC: 249,000 AED. A 7-seater luxury van.
We also have a 2007 Ferrari F430 and a 2026 Mercedes GLC 200 Coupe, though
their cash prices are currently unlisted."""


def test_luxury_reply_orders_cards_and_matches_variant_codes():
    cars = {  # insertion order is inventory-id order, unlike the reply
        2: car(2, "mercedes-benz", "glc-class", "2026 Mercedes GLC 200 Coupe 0KM"),
        7: car(7, "audi", "q8", "Audi Q8 55 TFSI Quattro S Line 2023"),
        15: car(15, "mercedes-benz", "v-class", "AED 3,580 P.M | 2024 Mercedes-Benz V220d 4MATIC"),
        23: car(23, "bmw", "m4", "2022 BMW M4 Competition, BMW Warranty"),
        44: car(44, "ferrari", "f430", "2007 | Ferrari | F430 (Car by Al Tayer)"),
        61: car(61, "bmw", "3-series", "BMW 330i M Sport 2025 5 Years Warranty"),
        88: car(88, "kia", "sorento", "Kia Sorento 2023 GCC"),  # searched, never mentioned
    }
    kept = _cars_mentioned_in_reply(cars, LUXURY_REPLY, None)
    assert [c["id"] for c in kept] == [7, 23, 61, 15, 44, 2]


def test_shared_spec_jargon_in_title_is_not_mention_evidence():
    # The V220d bullet says "4MATIC"; another car whose title also carries
    # "4Matic" must not ride in on that token.
    cars = {
        15: car(15, "mercedes-benz", "v-class", "2024 Mercedes-Benz V220d 4MATIC"),
        4: car(4, "mercedes-benz", "glc-class", "2020 Mercedes-Benz GLC 300 4Matic Coupe"),
    }
    reply = "The 2024 Mercedes-Benz V220d 4MATIC is a great 7-seater at 249,000 AED."
    kept = _cars_mentioned_in_reply(cars, reply, None)
    assert [c["id"] for c in kept] == [15]


# Live bug: reply named "2025 BMW M2" but no card rendered. The M2 is stored
# under the family model "2-series", so it matches only via a title variant
# code — and "M2" is 2 chars, which the code matcher used to reject (min 3).
M2_BUG_REPLY = """\
Here are some BMWs for you:

* 2025 BMW 330i M Sport: 225,000 AED.
* 2025 BMW M2: brand new, 1,194 km, 165,000 AED.
* 2024 BMW i4 eDrive35: 179,000 AED.
Let me know which interests you."""


def test_two_char_variant_code_m2_is_kept():
    cars = {
        59: car(59, "bmw", "2-series", "BMW M2 35 | Brand New | AED 2,650 monthly | Ref#S32015"),
        60: car(60, "bmw", "3-series", "BMW 330i M Sport - 5 Years Warranty"),
        79: car(79, "bmw", "i4", "2024 BMW i4 Gran Coupé eDrive35 MSport | GCC"),
    }
    kept = [c["id"] for c in _cars_mentioned_in_reply(cars, M2_BUG_REPLY, booking_prompt=None)]
    assert 59 in kept, "the named M2 must render a card"
    assert kept == [60, 59, 79], "cards follow reply mention order (330i, M2, i4)"


def test_engine_spec_lookalikes_do_not_false_match():
    # A reply asking about a '4L V6' engine must not vouch for a car whose
    # title merely contains those spec tokens and is otherwise unnamed.
    cars = {7: car(7, "ford", "territory", "Ford Territory 2.4L V6 Turbo")}
    kept = _cars_mentioned_in_reply(
        cars, "Do you have anything with a 4L V6 engine?", booking_prompt=None
    )
    assert kept == []
