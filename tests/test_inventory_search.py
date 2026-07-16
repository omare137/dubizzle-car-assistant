"""Tests for app.tools.inventory_search against the real data/processed/app.db.

Test values (make='bmw', price_max=200000, keyword='sunroof') were chosen by
inspecting the actual data: bmw has 9 rows — 6 with cash prices 165,000–329,000
and 3 with no extractable price — and 'sunroof' appears in 25 descriptions.
"""

from app.tools.inventory_search import (
    get_car_details,
    list_distinct_makes,
    search_inventory,
)


def test_filter_by_make_returns_only_matching_rows():
    results = search_inventory(make="bmw", limit=100)
    assert results, "expected bmw rows in the catalog"
    assert all(r["make"] == "bmw" for r in results)


def test_make_match_is_case_insensitive_and_partial():
    assert search_inventory(make="BMW", limit=100) == search_inventory(make="bmw", limit=100)
    results = search_inventory(make="merc", limit=100)
    assert results
    assert all(r["make"] == "mercedes-benz" for r in results)


def test_year_range_is_inclusive():
    results = search_inventory(year_min=2020, year_max=2022, limit=100)
    assert results
    assert all(2020 <= r["year"] <= 2022 for r in results)
    # Inclusivity: boundary years actually appear across the catalog.
    years = {r["year"] for r in search_inventory(year_min=2020, year_max=2020, limit=100)}
    assert years == {2020}


def test_price_max_keeps_unlisted_rows_and_drops_overpriced_rows():
    all_bmw = search_inventory(make="bmw", limit=100)
    capped = search_inventory(make="bmw", price_max=200_000, limit=100)

    priced = [r for r in capped if not r["price_unlisted"]]
    unlisted = [r for r in capped if r["price_unlisted"]]

    assert priced, "expected at least one bmw priced under 200k"
    assert all(r["price_aed_cash"] <= 200_000 for r in priced)
    # Design choice: null-price rows are included and flagged, not dropped.
    assert unlisted, "price_unlisted rows must survive a price_max filter"
    assert all(r["price_aed_cash"] is None for r in unlisted)
    # Rows with a real price over the max are excluded.
    over_max = [r for r in all_bmw if r["price_aed_cash"] and r["price_aed_cash"] > 200_000]
    assert over_max, "test needs an over-max row to be meaningful"
    capped_ids = {r["id"] for r in capped}
    assert all(r["id"] not in capped_ids for r in over_max)


def test_keywords_match_description_clean():
    results = search_inventory(keywords=["sunroof"], limit=100)
    assert results
    for r in results:
        # Check the full (untruncated) text — the 500-char summary may cut
        # the match out.
        full = get_car_details(r["id"])
        assert "sunroof" in full["description_clean"].lower()


def test_nonexistent_make_returns_empty_list():
    assert search_inventory(make="Yugo") == []


def test_duplicate_listings_are_deduped():
    # The dataset genuinely contains 5 Audi Q8 rows (ids 7,39,40,41,76) with
    # identical make/model/year/title — reposted ads. Search returns one.
    results = search_inventory(make="audi", model="q8", limit=100)
    assert len(results) == 1
    assert results[0]["id"] == 7  # lowest id of the group survives
    # Dedup is content-level: distinct listings of the same model still all appear.
    keys = [(r["make"], r["model"], r["year"], r["title"]) for r in search_inventory(limit=100)]
    assert len(keys) == len(set(keys)), "no composite-key duplicates in any result page"


def test_limit_and_deterministic_order():
    results = search_inventory(limit=5)
    assert len(results) == 5
    ids = [r["id"] for r in results]
    assert ids == sorted(ids)
    assert results == search_inventory(limit=5)


def test_search_result_truncates_description():
    results = search_inventory(limit=100)
    assert all(len(r["description_clean"]) <= 501 for r in results)  # 500 + ellipsis


def test_get_car_details_returns_full_row():
    details = get_car_details(1)
    assert details is not None
    assert details["id"] == 1
    assert set(details) >= {
        "make", "model", "trim", "year", "title", "description_clean",
        "price_aed_cash", "price_aed_monthly", "price_unlisted",
        "mileage_km", "photo_url",
    }


def test_get_car_details_none_for_missing_id():
    assert get_car_details(999_999) is None


def test_list_distinct_makes_sorted_and_real():
    makes = list_distinct_makes()
    assert makes == sorted(makes)
    assert len(makes) == len(set(makes))
    assert "bmw" in makes and "toyota" in makes
