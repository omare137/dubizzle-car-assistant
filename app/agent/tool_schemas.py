"""OpenAI-style function-calling schemas for the agent's tools (litellm format).

Implementations live in app/agent/tools_impl.py.
"""

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "search_inventory",
            "description": (
                "Search the dubizzle cars inventory. Returns real listings only — "
                "an empty list means nothing matches. Cars with price_unlisted=true "
                "have no listed price; say so instead of guessing."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "make": {
                        "type": "string",
                        "description": "Car make, partial match ok (e.g. 'mercedes', 'bmw', 'toyota')",
                    },
                    "model": {
                        "type": "string",
                        "description": "Car model, partial match ok (e.g. 'c-class', 'patrol')",
                    },
                    "year_min": {"type": "integer", "description": "Earliest model year, inclusive"},
                    "year_max": {"type": "integer", "description": "Latest model year, inclusive"},
                    "price_max": {
                        "type": "integer",
                        "description": "Max cash price in AED. Cars with no listed price are still included, flagged price_unlisted=true",
                    },
                    "keywords": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Feature/spec terms OR-matched against listing DESCRIPTION text only "
                            "(e.g. ['sunroof', 'SUV', '7 seats']). Do NOT put car makes or model "
                            "names here — they often don't appear in descriptions; use the make "
                            "and model parameters for those."
                        ),
                    },
                    "limit": {"type": "integer", "description": "Max results, default 10"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_car_details",
            "description": "Full details (untruncated description) for one car by its inventory id. Returns null if the id doesn't exist.",
            "parameters": {
                "type": "object",
                "properties": {
                    "car_id": {"type": "integer", "description": "Inventory id from a search result"},
                },
                "required": ["car_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_availability",
            "description": "Check whether a showroom viewing slot is valid. Viewings run Monday-Saturday, 08:00-20:00.",
            "parameters": {
                "type": "object",
                "properties": {
                    "car_id": {"type": "integer"},
                    "day": {"type": "string", "description": "Day of week, e.g. 'Wednesday'"},
                    "time": {"type": "string", "description": "24h HH:MM, e.g. '14:00'"},
                },
                "required": ["car_id", "day", "time"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_booking",
            "description": (
                "Propose a viewing slot for a specific car the user showed genuine interest in. "
                "The user will be shown a yes/no confirmation — do NOT treat the booking as made. "
                "Only call confirm_booking after the user explicitly says yes. "
                "car_id MUST come from a search_inventory or get_car_details call made EARLIER "
                "IN THIS SAME TURN — never a remembered or guessed id from earlier conversation "
                "turns. To book a previously-discussed car, first re-fetch it with "
                "get_car_details in this turn, then call propose_booking. Ids not confirmed "
                "this turn are rejected."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "car_id": {"type": "integer"},
                    "day": {"type": "string", "description": "Day of week, Monday-Saturday"},
                    "time": {"type": "string", "description": "24h HH:MM between 08:00 and 20:00"},
                },
                "required": ["car_id", "day", "time"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "confirm_booking",
            "description": "Record a confirmed viewing booking. Call ONLY after the user explicitly confirmed a proposed slot with a clear yes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "car_id": {"type": "integer"},
                    "day": {"type": "string"},
                    "time": {"type": "string"},
                    "session_id": {"type": "string", "description": "Current chat session id"},
                    "user_id": {"type": "integer", "description": "Logged-in user id, omit for guests"},
                },
                "required": ["car_id", "day", "time", "session_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_lead",
            "description": (
                "Persist what the user is looking for (budget range, needs) as a sales lead. "
                "Call once you've learned their budget and/or needs in conversation — "
                "no specific car required."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "Current chat session id"},
                    "user_id": {"type": "integer", "description": "Logged-in user id, omit for guests"},
                    "car_id": {"type": "integer", "description": "Car they're focused on, if any"},
                    "budget_range": {"type": "string", "description": "e.g. 'under 200,000 AED' or '100k-150k AED'"},
                    "needs_notes": {"type": "string", "description": "Free text: what they said they need (family SUV, low mileage, GCC specs...)"},
                },
                "required": ["session_id"],
            },
        },
    },
]
