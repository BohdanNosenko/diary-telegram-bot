import asyncio
import json
import math
import re
import shutil
from pathlib import Path

import reverse_geocoder as rg
import structlog

from vlog_journal.pipeline.registry import PipelineContext, register_step

logger = structlog.get_logger(__name__)

def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance in meters between two GPS coordinates using Haversine formula."""
    r = 6371000.0  # Earth radius in meters
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = math.sin(delta_phi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return r * c

def parse_iso6709_gps(location_str: str) -> tuple[float, float] | None:
    """Parse ISO 6709 or standard ffprobe location string like '+40.7128-074.0060/'."""
    if not location_str:
        return None

    # Matches +40.7128-074.0060 or -33.8688+151.2093 or 40.7128, -74.0060
    pattern = r"([+-]?\d+\.\d+)\s*([+-]\d+\.\d+)"
    match = re.search(pattern, location_str)
    if match:
        try:
            lat = float(match.group(1))
            lon = float(match.group(2))
            if -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0:
                return lat, lon
        except ValueError:
            pass
    return None

async def _extract_clip_gps(clip_path: str) -> tuple[float, float] | None:
    """Extract location string from video metadata via ffprobe."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None

    cmd = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format_tags=location:format_tags=location-eng:stream_tags=location",
        "-of",
        "json",
        str(clip_path),
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await proc.communicate()
        data = json.loads(stdout.decode("utf-8", errors="replace"))

        tags = data.get("format", {}).get("tags", {})
        loc_str = tags.get("location") or tags.get("location-eng")
        if not loc_str:
            for stream in data.get("streams", []):
                s_tags = stream.get("tags", {})
                loc_str = s_tags.get("location") or s_tags.get("location-eng")
                if loc_str:
                    break

        if loc_str:
            return parse_iso6709_gps(loc_str)
    except Exception as e:
        logger.warning("GPS ffprobe extraction failed", path=clip_path, error=str(e))

    return None

@register_step("enrichment.extract_gps")
async def extract_gps(ctx: PipelineContext) -> PipelineContext:
    """Extract GPS coordinates from session clips and deduplicate by proximity."""
    clips = ctx.payload.get("clips", [])
    enrich_cfg = getattr(ctx.config, "enrichment", None) if ctx.config else None
    prox_val = getattr(enrich_cfg, "gps_proximity_radius_meters", 500) if enrich_cfg else 500
    proximity_radius_m = prox_val if isinstance(prox_val, (int, float)) else 500

    extracted_points = []
    for i, clip in enumerate(clips):
        path = clip.get("path")
        if path and Path(path).exists():
            coords = await _extract_clip_gps(path)
            if coords:
                extracted_points.append((coords[0], coords[1], i + 1))

    if not extracted_points:
        logger.info("No GPS coordinates extracted from session clips")
        ctx.payload["locations_visited"] = []
        return ctx

    # Deduplicate by proximity radius
    clusters = []
    for lat, lon, clip_idx in extracted_points:
        merged = False
        for cluster in clusters:
            dist = haversine_distance(lat, lon, cluster["lat"], cluster["lon"])
            if dist <= proximity_radius_m:
                cluster["clips"].append(clip_idx)
                cluster["count"] += 1
                merged = True
                break
        if not merged:
            clusters.append(
                {
                    "gps": [round(lat, 4), round(lon, 4)],
                    "lat": lat,
                    "lon": lon,
                    "clips": [clip_idx],
                    "count": 1,
                }
            )

    locations_visited = []
    for c in clusters:
        locations_visited.append(
            {
                "gps": c["gps"],
                "clips": c["clips"],
                "time_range": f"Clips {', '.join(map(str, c['clips']))}"
                if len(c["clips"]) > 1
                else f"Clip {c['clips'][0]}",
            }
        )

    ctx.payload["locations_visited"] = locations_visited
    logger.info("GPS extraction complete", locations_count=len(locations_visited))
    return ctx

@register_step("enrichment.reverse_geocode")
async def reverse_geocode(ctx: PipelineContext) -> PipelineContext:
    """Resolve GPS coordinates in locations_visited to human-readable names."""
    locations_visited = ctx.payload.get("locations_visited", [])
    if not locations_visited:
        ctx.payload["primary_location"] = None
        return ctx

    # Reverse geocode offline
    for loc in locations_visited:
        gps = loc.get("gps")
        if gps and len(gps) == 2:
            lat, lon = gps[0], gps[1]
            try:
                res_list = rg.search((lat, lon))
                if res_list:
                    res = res_list[0]
                    city = res.get("name", "")
                    admin = res.get("admin1", "")
                    if city and admin and city != admin:
                        loc_name = f"{city}, {admin}"
                    elif city:
                        loc_name = city
                    else:
                        loc_name = f"{lat}, {lon}"
                    loc["name"] = loc_name
            except Exception as e:
                logger.warning("Reverse geocoding failed", gps=gps, error=str(e))
                loc["name"] = f"{lat}, {lon}"

    # Pick primary_location: location with most clips
    sorted_locs = sorted(locations_visited, key=lambda x: len(x.get("clips", [])), reverse=True)
    primary_loc = sorted_locs[0].get("name") if sorted_locs else None
    ctx.payload["primary_location"] = primary_loc
    logger.info("Reverse geocoding complete", primary_location=primary_loc)
    return ctx
