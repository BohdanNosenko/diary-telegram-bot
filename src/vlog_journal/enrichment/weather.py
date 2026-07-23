import httpx
import structlog

from vlog_journal.pipeline.registry import PipelineContext, register_step

logger = structlog.get_logger(__name__)

WMO_WEATHER_CODES: dict[int, str] = {
    0: "clear sky",
    1: "mainly clear",
    2: "partly cloudy",
    3: "overcast",
    45: "fog",
    48: "depositing rime fog",
    51: "light drizzle",
    53: "moderate drizzle",
    55: "dense drizzle",
    56: "freezing drizzle",
    57: "dense freezing drizzle",
    61: "slight rain",
    63: "moderate rain",
    65: "heavy rain",
    66: "freezing rain",
    67: "heavy freezing rain",
    71: "slight snow",
    73: "moderate snow",
    75: "heavy snow",
    77: "snow grains",
    80: "slight rain showers",
    81: "moderate rain showers",
    82: "violent rain showers",
    85: "slight snow showers",
    86: "heavy snow showers",
    95: "thunderstorm",
    96: "thunderstorm with hail",
    99: "heavy thunderstorm with hail",
}

def map_wmo_code(code: int) -> str:
    """Map WMO weather code to human-readable string."""
    return WMO_WEATHER_CODES.get(code, "partly cloudy")

@register_step("enrichment.fetch_weather")
async def fetch_weather(ctx: PipelineContext) -> PipelineContext:
    """Fetch weather data from Open-Meteo for visited locations on entry_date."""
    locations_visited = ctx.payload.get("locations_visited", [])
    entry_date = ctx.payload.get("entry_date")

    if not locations_visited or not entry_date:
        logger.info("Skipping weather fetch: missing locations or entry_date")
        ctx.payload["primary_weather"] = None
        return ctx

    primary_weather = None

    async with httpx.AsyncClient(timeout=10.0) as client:
        for i, loc in enumerate(locations_visited):
            gps = loc.get("gps")
            if not gps or len(gps) != 2:
                loc["weather"] = None
                continue

            lat, lon = gps[0], gps[1]
            url = (
                f"https://api.open-meteo.com/v1/forecast"
                f"?latitude={lat}&longitude={lon}"
                f"&daily=temperature_2m_max,temperature_2m_min,weathercode"
                f"&timezone=auto&start_date={entry_date}&end_date={entry_date}"
            )

            try:
                resp = await client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    daily = data.get("daily", {})
                    t_max = daily.get("temperature_2m_max", [None])[0]
                    w_code = daily.get("weathercode", [None])[0]

                    if t_max is not None and w_code is not None:
                        w_desc = map_wmo_code(int(w_code))
                        weather_str = f"{round(t_max)}°C, {w_desc}"
                        loc["weather"] = weather_str
                        if i == 0 or loc.get("name") == ctx.payload.get("primary_location"):
                            primary_weather = weather_str
                    else:
                        loc["weather"] = None
                else:
                    logger.warning("Open-Meteo weather request non-200", status=resp.status_code)
                    loc["weather"] = None
            except Exception as e:
                logger.warning("Weather fetch failed (non-fatal)", gps=gps, error=str(e))
                loc["weather"] = None

    ctx.payload["primary_weather"] = primary_weather
    logger.info("Weather fetch complete", primary_weather=primary_weather)
    return ctx
