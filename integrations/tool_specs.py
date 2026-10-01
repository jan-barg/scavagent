"""Model-facing definitions for the place, research, and routing adapters.

tools.py registers PLACE_TOOLS and PLACE_TOOL_MAP. The adapters return tool_ok/tool_error dicts;
the harness sends them to the model as JSON text and keeps the dict in the /chat trace.
"""

from integrations.geocoding import geocode_place
from integrations.mta import get_transit_arrivals
from integrations.research import find_places, research_place
from integrations.routes import get_route

_POINT = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "description": "Stable id, e.g. 'start', 'destination', or a place_id."},
        "lat": {"type": "number"},
        "lng": {"type": "number"},
        "dwell_minutes": {"type": "number", "description": "Minutes spent at this stop before leaving it. Default 0."},
    },
    "required": ["id", "lat", "lng"],
}

PLACE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "geocode_place",
            "description": (
                "Resolve a place the user typed in New York City to coordinates. Accepts a cross street "
                "('Central Park West and West 86th Street'), a street address ('1 West 72nd Street'), or a "
                "landmark ('American Museum of Natural History'). Returns data.location (use it as the start, "
                "destination, or a required stop) and match_type (intersection, address, or place). "
                "If warnings are present, confirm the match with the user."
            ),
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string", "description": "Only the place, as the user described it."}},
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_places",
            "description": (
                "Find real places near a point that could become adventure stops: places documented on Wikipedia "
                "and designated NYC landmarks, each with a place_id for research_place. distance_m is straight-line. "
                "Without a query, nearest first. With query, each key term is searched separately and each candidate "
                "lists the matched_terms its article mentions, most matches first; a warning says when nothing "
                "nearby matches. Results are leads, not proof that a place suits an activity or is open."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "lat": {"type": "number"},
                    "lng": {"type": "number"},
                    "radius_m": {"type": "integer", "description": "Search radius in meters, 100-2000. Default 800."},
                    "query": {"type": "string", "description": (
                        "Optional key terms of a real-subject theme, e.g. 'Strokes rock \"music venue\"' or 'jazz'. "
                        "Quote a phrase to keep it whole. Skip generic words like 'historic' or 'landmark'.")},
                    "limit": {"type": "integer", "description": "Maximum candidates, 1-25. Default 15."},
                },
                "required": ["lat", "lng"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "research_place",
            "description": (
                "Get sourced facts about one place from find_places before using it in the story. Returns "
                "data.evidence: claims quoted from Wikipedia (pinned to the exact revision) and NYC Landmarks "
                "Preservation Commission records, each with a claim_id and source URL. State as historical fact only "
                "what these claims say; everything else is the adventure's fiction. No claim establishes a physical "
                "feature that is visible today, so never promise a plaque, inscription, or detail is there."
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
                "Directions and timing through stops in visiting order, one leg per consecutive pair, on foot or by "
                "subway/bus. With transit allowed, a leg rides only when it beats walking by several minutes; "
                "transit legs name the line, stations, departure time, and leave-by time. Legs are timed from "
                "depart_at plus each stop's dwell_minutes. Durations exclude contingency. Many Manhattan sidewalks "
                "are unnamed ('the walkway'); describe walks with details.via_streets, heading, and cross streets."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "stops": {"type": "array", "items": _POINT, "description": "2-10 points in visiting order."},
                    "modes": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["walk", "transit"]},
                        "description": "Modes the user allows. Default ['walk', 'transit'].",
                    },
                    "depart_at": {"type": "string", "description": "ISO time the user is ready to leave. Default now."},
                    "transit_types": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["subway", "bus"]},
                        "description": "Transit the user accepts. Default both.",
                    },
                },
                "required": ["stops"],
            },
        },
    },
]

PLACE_TOOLS.append({
    "type": "function",
    "function": {
        "name": "get_transit_arrivals",
        "description": (
            "Live next subway arrivals at one station, by direction (e.g. Uptown/Downtown), with where each train is "
            "heading and the lines' active service alerts. Use it when the user is about to ride or asks when the next "
            "train comes. Pass the station name from the transit directions ('86 St'), plus line or lat/lng to pick "
            "among stations that share a name."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "station": {"type": "string", "description": "Station name as signs show it, e.g. '86 St'."},
                "line": {"type": "string", "description": "One route, e.g. 'A' or '1'."},
                "direction": {"type": "string", "description": "'uptown' or 'downtown' (or the sign's label)."},
                "lat": {"type": "number", "description": "Near this point, to pick among same-named stations."},
                "lng": {"type": "number"},
            },
            "required": ["station"],
        },
    },
})

PLACE_TOOL_MAP = {
    "geocode_place": geocode_place,
    "find_places": find_places,
    "research_place": research_place,
    "get_route": get_route,
    "get_transit_arrivals": get_transit_arrivals,
}
