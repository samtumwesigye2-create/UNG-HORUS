import os
os.environ.setdefault('HORUS_API_KEY','test-key')

from fastapi.testclient import TestClient
from main import app

client = TestClient(app)
HEADERS={'x-api-key':'test-key'}
ISS_LINE1='1 25544U 98067A   24001.50000000  .00016717  00000+0  30270-3 0  9999'
ISS_LINE2='2 25544  51.6416  21.0000 0005000 120.0000 300.0000 15.50000000400000'


def test_health():
    assert client.get('/health').status_code == 200


def test_register_and_look_angle():
    r = client.post('/orbit/tle', headers=HEADERS, json={
        'satellite_id':'iss','name':'ISS','line1':ISS_LINE1,'line2':ISS_LINE2
    })
    assert r.status_code == 200
    r = client.post('/orbit/satellites/iss/look-angle', headers=HEADERS, json={
        'observer':{'latitude_deg':0.3476,'longitude_deg':32.5825,'altitude_m':1200}
    })
    assert r.status_code == 200
    body = r.json()
    assert 'azimuth_deg' in body and 'elevation_deg' in body and 'range_km' in body


def test_pass_predictions():
    client.post('/orbit/tle', headers=HEADERS, json={
        'satellite_id':'iss','name':'ISS','line1':ISS_LINE1,'line2':ISS_LINE2
    })
    r = client.post('/orbit/satellites/iss/passes', headers=HEADERS, json={
        'observer':{'latitude_deg':0.3476,'longitude_deg':32.5825,'altitude_m':1200},
        'hours':24,'min_elevation_deg':0
    })
    assert r.status_code == 200
    assert 'events' in r.json()
