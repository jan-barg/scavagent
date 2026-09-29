"""Model-facing definitions for the integration adapters, ready for the central registry.

Registration stays in tools.py: add TOOL_SPECS to TOOLS and TOOL_FUNCTIONS to TOOL_MAP. The adapters
return envelope dicts; serialize them to a JSON string for the model message and keep the dict in
the /chat trace.
"""

from integrations.geocoding import geocode_place
from integrations.research import find_places, research_place
from integrations.routes import get_route, get_walking_times

POINT = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "description": "Stable ID, e.g. 'start' or a place_id from find_places."},
        "lat": {"type": "number"},
        "lon": {"type": "number"},
    },
    "required": ["id", "lat", "lon"],
}

TOOL_SPECS = [
    {
        "type": "function",
        "function": {
            "name": "geocode_place",
            "description": (
                "Resolve a place the user typed in New York City to coordinates. Accepts a cross street "
                "('Central Park West and West 86th Street'), a street address ('1 West 72nd Street'), or a "
                "landmark ('American Museum of Natural History'). Call it before planning from any typed "
                "location. Returns label, lat, lon, and match_type (intersection, address, or place). "
                "If warnings are present, confirm the match with the user."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Only the place, as the user described it."},
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_places",
            "description": (
                "Find real places near a point that could become adventure stops: places documented on "
                "Wikipedia and designated NYC landmarks, nearest first, each with a place_id for "
                "research_place. distance_m is straight-line. Use query to lean toward a theme word "
                "('architecture', 'jazz'); omit it for a general search. Results are leads, not proof that "
                "a place suits an activity or is open."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "lat": {"type": "number"},
                    "lon": {"type": "number"},
                    "radius_m": {"type": "integer", "description": "Search radius in meters, 100-2000. Default 800."},
                    "query": {"type": "string", "description": "Optional theme word or phrase."},
                    "limit": {"type": "integer", "description": "Maximum candidates, 1-25. Default 15."},
                },
                "required": ["lat", "lon"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "research_place",
            "description": (
                "Get sourced facts about one place from find_places before using it in the story. Returns "
                "claims quoted from Wikipedia (pinned to the exact revision) and NYC Landmarks Preservation "
                "Commission records, each with a source URL. State as historical fact only what these claims "
                "say; present everything else as the adventure's fiction. Physical features are not verified, "
                "so never promise that a plaque, inscription, or detail is there."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "place_id": {"type": "string", "description": "A place_id from find_places, e.g. 'wiki:9238071'."},
                    "focus": {"type": "string", "description": "Optional topic, e.g. 'history' or 'architecture'."},
                },
                "required": ["place_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_route",
            "description": (
                "Walking route through stops in visiting order: one leg per consecutive pair with minutes, "
                "meters, heading, named streets, and turn instructions. Only walking can be routed; transit "
                "is not configured. Durations exclude time spent at stops and waits at crossings, so add "
                "activity time and contingency. Many Manhattan sidewalks are unnamed ('the walkway'); "
                "describe legs with via_streets, heading, and cross streets instead."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "stops": {"type": "array", "items": POINT, "description": "2-10 points in visiting order."},
                    "modes": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["walk", "transit", "car"]},
                        "description": "Modes the user allows. Default ['walk'].",
                    },
                },
                "required": ["stops"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_walking_times",
            "description": (
                "Walking minutes from one point to up to 25 destinations. Use it to rank or filter candidate "
                "stops by real travel time instead of straight-line distance."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "origin": POINT,
                    "destinations": {"type": "array", "items": POINT, "description": "1-25 points."},
                },
                "required": ["origin", "destinations"],
            },
        },
    },
]

TOOL_FUNCTIONS = {
    "geocode_place": geocode_place,
    "find_places": find_places,
    "research_place": research_place,
    "get_route": get_route,
    "get_walking_times": get_walking_times,
}
