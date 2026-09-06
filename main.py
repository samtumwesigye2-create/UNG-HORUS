from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
from skyfield.api import EarthSatellite, load, wgs84

APP_VERSION = "0.2.0"
API_KEY = os.getenv("HORUS_API_KEY", "").strip()
PULSAR_BASE_URL = os.getenv("PULSAR_BASE_URL", "").strip().rstrip("/")
PULSAR_API_KEY = os.getenv("PULSAR_API_KEY", "").strip()
PULSAR_EVENT_PATH = os.getenv("PULSAR_EVENT_PATH", "/events").strip() or "/events"
HERMES_BASE_URL = os.getenv("HERMES_BASE_URL", "").strip().rstrip("/")
HERMES_API_KEY = os.getenv("HERMES_API_KEY", "").strip()
SENSOR_FUSION_BASE_URL = os.getenv("SENSOR_FUSION_BASE_URL", "").strip().rstrip("/")
SENSOR_FUSION_API_KEY = os.getenv("SENSOR_FUSION_API_KEY", "").strip()

app = FastAPI(title="UNG-HORUS Orbital Operations", version=APP_VERSION)
ts = load.timescale()

class TLEIn(BaseModel):
    satellite_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    line1: str = Field(min_length=60, max_length=80)
    line2: str = Field(min_length=60, max_length=80)

class ObserverIn(BaseModel):
    latitude_deg: float = Field(ge=-90, le=90)
    longitude_deg: float = Field(ge=-180, le=180)
    altitude_m: float = Field(default=0, ge=-500, le=10000)

class LookAngleRequest(BaseModel):
    observer: ObserverIn
    at: datetime | None = None

class PassRequest(BaseModel):
    observer: ObserverIn
    hours: int = Field(default=24, ge=1, le=168)
    min_elevation_deg: float = Field(default=10.0, ge=0, le=90)

class DopplerRequest(BaseModel):
    observer: ObserverIn
    nominal_frequency_hz: float = Field(gt=0)
    at: datetime | None = None

class RadioTrackRequest(DopplerRequest):
    apply_to_radio: bool = True
    publish_state: bool = True
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    ttl_seconds: int = Field(default=30, ge=1, le=300)

SATELLITES: dict[str, EarthSatellite] = {}
META: dict[str, dict[str, Any]] = {}

def _auth(x_api_key: str):
    if not API_KEY:
        raise HTTPException(503, "HORUS_API_KEY is not configured")
    if not hmac.compare_digest(x_api_key or "", API_KEY):
        raise HTTPException(401, "Invalid API key")

def _satellite(satellite_id: str) -> EarthSatellite:
    sat = SATELLITES.get(satellite_id)
    if sat is None:
        raise HTTPException(404, "Satellite not found")
    return sat

def _sf_time(dt: datetime | None):
    dt = dt or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return ts.from_datetime(dt.astimezone(timezone.utc))

def _observer(obs: ObserverIn):
    return wgs84.latlon(obs.latitude_deg, obs.longitude_deg, elevation_m=obs.altitude_m)

def _doppler_payload(satellite_id: str, body: DopplerRequest) -> dict[str, Any]:
    sat = _satellite(satellite_id)
    t = _sf_time(body.at)
    observer = _observer(body.observer)
    topo = (sat - observer).at(t)
    _, _, _, _, _, range_rate = topo.frame_latlon_and_rates(observer)
    radial_m_s = range_rate.m_per_s
    c = 299_792_458.0
    shift_hz = -(radial_m_s / c) * body.nominal_frequency_hz
    return {
        "satellite_id": satellite_id,
        "timestamp": t.utc_iso(),
        "nominal_frequency_hz": body.nominal_frequency_hz,
        "range_rate_m_s": radial_m_s,
        "doppler_shift_hz": shift_hz,
        "corrected_frequency_hz": body.nominal_frequency_hz + shift_hz,
    }

def _post_json(url: str, payload: dict[str, Any], api_key: str = "") -> dict[str, Any]:
    headers = {"x-api-key": api_key} if api_key else {}
    with httpx.Client(timeout=3.0) as client:
        response = client.post(url, json=payload, headers=headers)
        response.raise_for_status()
        try:
            return response.json()
        except Exception:
            return {"status_code": response.status_code}

def _publish_state(event_type: str, value: dict[str, Any], confidence: float, ttl_seconds: int) -> dict[str, Any]:
    results: dict[str, Any] = {}
    message_id = str(uuid.uuid4())
    now = time.time()
    if SENSOR_FUSION_BASE_URL and SENSOR_FUSION_API_KEY:
        try:
            results["sensor_fusion"] = _post_json(
                f"{SENSOR_FUSION_BASE_URL}/ingest",
                {"source": "ung-horus", "data_type": event_type, "value": value, "message_id": message_id,
                 "timestamp": now, "confidence": confidence, "ttl_seconds": ttl_seconds},
                SENSOR_FUSION_API_KEY,
            )
        except Exception as exc:
            results["sensor_fusion"] = {"accepted": False, "error": str(exc)}
    if PULSAR_BASE_URL:
        try:
            results["pulsar"] = _post_json(
                f"{PULSAR_BASE_URL}{PULSAR_EVENT_PATH}",
                {"event_type": event_type, "source": "ung-horus", "message_id": message_id,
                 "timestamp": now, "confidence": confidence, "ttl_seconds": ttl_seconds, "payload": value},
                PULSAR_API_KEY,
            )
        except Exception as exc:
            results["pulsar"] = {"accepted": False, "error": str(exc)}
    return results

@app.get("/health")
def health():
    return {"status": "ok", "service": "ung-horus", "version": APP_VERSION}

@app.get("/ready")
def ready():
    return {"ready": bool(API_KEY), "api_key_configured": bool(API_KEY), "satellite_count": len(SATELLITES),
            "pulsar_configured": bool(PULSAR_BASE_URL), "hermes_configured": bool(HERMES_BASE_URL and HERMES_API_KEY),
            "sensor_fusion_configured": bool(SENSOR_FUSION_BASE_URL and SENSOR_FUSION_API_KEY)}

@app.post("/orbit/tle")
def register_tle(body: TLEIn, x_api_key: str = Header(default="")):
    _auth(x_api_key)
    sat = EarthSatellite(body.line1, body.line2, body.name, ts)
    SATELLITES[body.satellite_id] = sat
    META[body.satellite_id] = {"name": body.name, "line1": body.line1, "line2": body.line2,
                               "registered_at": datetime.now(timezone.utc).isoformat()}
    return {"registered": True, "satellite_id": body.satellite_id, "name": body.name}

@app.get("/orbit/satellites")
def list_satellites(x_api_key: str = Header(default="")):
    _auth(x_api_key)
    return [{"satellite_id": sid, **META[sid]} for sid in SATELLITES]

@app.get("/orbit/satellites/{satellite_id}/position")
def position(satellite_id: str, x_api_key: str = Header(default="")):
    _auth(x_api_key)
    sat = _satellite(satellite_id); t = _sf_time(None); geo = sat.at(t); sub = wgs84.subpoint(geo)
    return {"satellite_id": satellite_id, "timestamp": t.utc_iso(), "latitude_deg": sub.latitude.degrees,
            "longitude_deg": sub.longitude.degrees, "altitude_km": sub.elevation.km,
            "eci_km": list(geo.position.km), "velocity_km_s": list(geo.velocity.km_per_s)}

@app.post("/orbit/satellites/{satellite_id}/look-angle")
def look_angle(satellite_id: str, body: LookAngleRequest, x_api_key: str = Header(default="")):
    _auth(x_api_key)
    sat = _satellite(satellite_id); t = _sf_time(body.at); topocentric = (sat - _observer(body.observer)).at(t)
    alt, az, distance = topocentric.altaz()
    return {"satellite_id": satellite_id, "timestamp": t.utc_iso(), "azimuth_deg": az.degrees,
            "elevation_deg": alt.degrees, "range_km": distance.km, "visible": alt.degrees > 0}

@app.post("/orbit/satellites/{satellite_id}/passes")
def passes(satellite_id: str, body: PassRequest, x_api_key: str = Header(default="")):
    _auth(x_api_key)
    sat = _satellite(satellite_id); observer = _observer(body.observer)
    start = datetime.now(timezone.utc); end = start + timedelta(hours=body.hours)
    times, events = sat.find_events(observer, ts.from_datetime(start), ts.from_datetime(end), altitude_degrees=body.min_elevation_deg)
    labels = {0: "AOS", 1: "MAX", 2: "LOS"}; out = []
    for t, event in zip(times, events):
        topocentric = (sat - observer).at(t); alt, az, distance = topocentric.altaz()
        out.append({"event": labels[int(event)], "timestamp": t.utc_iso(), "azimuth_deg": az.degrees,
                    "elevation_deg": alt.degrees, "range_km": distance.km})
    return {"satellite_id": satellite_id, "events": out}

@app.post("/orbit/satellites/{satellite_id}/doppler")
def doppler(satellite_id: str, body: DopplerRequest, x_api_key: str = Header(default="")):
    _auth(x_api_key)
    return _doppler_payload(satellite_id, body)

@app.post("/orbit/satellites/{satellite_id}/track-radio")
def track_radio(satellite_id: str, body: RadioTrackRequest, x_api_key: str = Header(default="")):
    _auth(x_api_key)
    orbital = _doppler_payload(satellite_id, body)
    result: dict[str, Any] = {"orbital": orbital, "radio": {"applied": False}, "published": {}}
    if body.apply_to_radio:
        if not (HERMES_BASE_URL and HERMES_API_KEY):
            raise HTTPException(503, "HERMES integration is not configured")
        try:
            result["radio"] = _post_json(
                f"{HERMES_BASE_URL}/radio/doppler/apply",
                {"nominal_frequency_hz": int(round(body.nominal_frequency_hz)), "correction_hz": orbital["doppler_shift_hz"]},
                HERMES_API_KEY,
            )
        except Exception as exc:
            raise HTTPException(502, f"HERMES radio control failed: {exc}")
    if body.publish_state:
        result["published"] = _publish_state("satellite.radio_tracking", orbital, body.confidence, body.ttl_seconds)
    return result
