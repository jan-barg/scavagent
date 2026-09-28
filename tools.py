"""The tools the harness can run, and the JSON that describes them to the model."""

from datetime import datetime, timezone

import requests

from schemas import Freshness, tool_error, tool_ok

# Open-Meteo is free and needs no API key.
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"


def get_weather(location: str) -> dict:
    """Get the current weather for a location."""
    try:
        places = requests.get(GEOCODE_URL, params={"name": location, "count": 1}, timeout=10).json()
        if not places.get("results"):
            return tool_error(
                "NO_MATCH",
                f"City '{location}' was not found.",
                retryable=False,
                next_step="Retry with a plain city name such as 'New York'.",
            )
        place = places["results"][0]

        current = requests.get(
            FORECAST_URL,
            params={
                "latitude": place["latitude"],
                "longitude": place["longitude"],
                "current": "temperature_2m,relative_humidity_2m,wind_speed_10m",
                "temperature_unit": "fahrenheit",
                "wind_speed_unit": "mph",
            },
            timeout=10,
        ).json()["current"]
    except (requests.RequestException, KeyError, ValueError) as e:
        # The model cannot see an exception. Return something it can reason about.
        return tool_error(
            "UPSTREAM_UNAVAILABLE",
            f"Weather service failed: {type(e).__name__}",
            retryable=True,
            next_step="Answer without the weather, or try once more.",
        )

    return tool_ok(
        {
            "location": place["name"],
            "temp_f": current["temperature_2m"],
            "humidity": current["relative_humidity_2m"],
            "wind_mph": current["wind_speed_10m"],
        },
        freshness=Freshness(kind="live", retrieved_at=datetime.now(timezone.utc)),
    )


# What the model sees: the "set notes" in the screenplay.
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get the current weather (temperature, humidity, wind) for a city.",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {"type": "string", "description": "City name, e.g. 'New York'"},
                },
                "required": ["location"],
            },
        },
    },
]

# What the harness runs: tool name -> Python function.
TOOL_MAP = {"get_weather": get_weather}


def run_tool(name: str, args: dict) -> dict:
    """Run one tool call. Models invent tool names and arguments; never let that crash the loop."""
    if name not in TOOL_MAP:
        return tool_error(
            "UNKNOWN_TOOL",
            f"Unknown tool '{name}'.",
            retryable=False,
            next_step=f"Use one of: {', '.join(TOOL_MAP)}.",
        )
    try:
        return TOOL_MAP[name](**args)
    except TypeError as e:
        return tool_error(
            "INVALID_ARGUMENT",
            f"Bad arguments for {name}: {e}",
            retryable=False,
            next_step="Call the tool again with the arguments its schema lists.",
        )
    except Exception as e:
        return tool_error(
            "INTERNAL_ERROR",
            f"{name} failed unexpectedly: {type(e).__name__}",
            retryable=False,
            next_step="Continue without this tool's result and tell the user it is unavailable.",
        )
