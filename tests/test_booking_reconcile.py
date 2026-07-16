"""Tests for the orchestrator's booking-prompt/reply reconciliation — no LLM.

The confirmation widget must show the same slot the reply text offers.
Reproduces the live bug: propose_booking succeeded with 20:00 (closing time),
then the reply offered "Thursday at 7:30 PM instead" without re-proposing,
and the widget asked "Thursday at 20:00?".

Uses real inventory ids (car 7 exists) since slot correction re-validates
through check_availability.
"""

from app.agent.orchestrator import _reconcile_booking_prompt, _slots_in_reply

BP = {"car_id": 7, "day": "Thursday", "time": "20:00"}

# The actual reply from the live bug report (abridged).
LIVE_MISMATCH_REPLY = """\
I apologize for the oversight. Since our showrooms close at 8:00 PM, I can
offer you a booking for Thursday at 7:30 PM instead, which is the latest slot
available that day.

Would you like me to book this Thursday at 7:30 PM for the 2023 Audi Q8?
Please confirm with a "yes" if this works for you."""


def test_live_mismatch_is_corrected_to_reply_slot():
    fixed = _reconcile_booking_prompt(dict(BP), LIVE_MISMATCH_REPLY)
    assert fixed == {"car_id": 7, "day": "Thursday", "time": "19:30"}


def test_matching_slot_is_kept():
    bp = {"car_id": 7, "day": "Wednesday", "time": "14:00"}
    reply = "Would Wednesday at 14:00 work for you to come see it?"
    assert _reconcile_booking_prompt(dict(bp), reply) == bp


def test_12h_phrasing_of_same_slot_is_kept():
    bp = {"car_id": 7, "day": "Wednesday", "time": "14:00"}
    reply = "Would Wednesday at 2:00 PM work for you?"
    assert _reconcile_booking_prompt(dict(bp), reply) == bp


def test_time_before_day_phrasing_matches():
    bp = {"car_id": 7, "day": "Friday", "time": "10:00"}
    reply = "Shall I book you in at 10:00 AM on Friday?"
    assert _reconcile_booking_prompt(dict(bp), reply) == bp


def test_reply_with_no_slot_keeps_prompt():
    reply = "Shall we lock that in? Just say yes and I'll confirm the viewing."
    assert _reconcile_booking_prompt(dict(BP), reply) == BP


def test_invalid_corrected_slot_drops_prompt():
    reply = "Would Sunday at 10:00 work for you?"  # Sundays are closed
    assert _reconcile_booking_prompt(dict(BP), reply) is None


def test_ambiguous_multiple_slots_drop_prompt():
    reply = "I could do Wednesday at 10:00 or Friday at 16:00 — which suits you?"
    assert _reconcile_booking_prompt(dict(BP), reply) is None


def test_none_stays_none():
    assert _reconcile_booking_prompt(None, "any reply") is None


def test_slot_parsing_ignores_dayless_times_and_sentence_gaps():
    slots = _slots_in_reply(LIVE_MISMATCH_REPLY)
    # "close at 8:00 PM" has no day attached and must not become a slot.
    assert slots == {("thursday", "19:30")}


def test_slot_parsing_formats():
    assert _slots_in_reply("Monday at 08:00, then 9:30 pm on Tuesday") == {
        ("monday", "08:00"),
        ("tuesday", "21:30"),
    }
    assert _slots_in_reply("Saturday at 12 PM sharp") == {("saturday", "12:00")}
