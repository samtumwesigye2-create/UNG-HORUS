# UNG-HORUS

UNG-HORUS is the Uganda National Grid orbital and aerial operations service.

## Current orbital capabilities
- TLE registration
- SGP4 propagation through Skyfield/sgp4
- real-time sub-satellite position
- look angles: azimuth, elevation and range
- pass prediction with AOS/MAX/LOS events
- Doppler shift and corrected-frequency calculation
- REST API health/readiness endpoints

## Integration boundaries
HORUS owns orbital calculations and pass geometry. HERMES owns physical radio control and Hamlib/rigctld integration. PULSAR carries orbital/radio events. Sensor Fusion can combine predicted orbital state with measured telemetry. ATLAS supervises orchestration.

## Safety/reliability
HORUS must not become a hard dependency for UGAMAP, UGASHIP, VECTOR or other business systems. They continue operating when HORUS is unavailable; HORUS provides additional operational data when available.

## Environment
- `HORUS_API_KEY` required for protected endpoints
- `PULSAR_BASE_URL` optional until wiring
- `HERMES_BASE_URL` optional until radio-control wiring
- `SENSOR_FUSION_BASE_URL` optional until telemetry wiring
