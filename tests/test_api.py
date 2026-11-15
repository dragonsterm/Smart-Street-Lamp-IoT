import unittest
import dataclasses
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
from server.app import Settings, create_app
from server.database import Database


class ApiTest(unittest.TestCase):
    def setUp(self):
        live = Settings.load()
        from server.app import read_env, ROOT
        test_name = read_env(ROOT / ".env").get("MYSQL_TEST_DATABASE", "smart_street_lamp_test")
        if not test_name.endswith("_test") or test_name == live.database.database:
            raise RuntimeError("Refusing to clean a non-test MySQL database")
        config = dataclasses.replace(live.database, database=test_name)
        database = Database(config)
        database.initialize("streetlamp-01")
        with database.session() as session:
            for table in ("commands", "telemetry", "boots", "devices"):
                session.execute("DELETE FROM " + table)
        self.settings = Settings(config, "streetlamp-01",
                                 "device-token-for-tests-only", "admin", "admin",
                                 "session-secret-for-tests-only")
        self.app = create_app(self.settings)
        self.now = 1_800_000_000_000
        self.app.state.clock = lambda: self.now
        self.client = TestClient(self.app)
        self.auth = {"Authorization": "Bearer " + self.settings.device_token}
        self.sequence = 0

    def tearDown(self):
        self.client.close()

    def login(self):
        result = self.client.post("/api/login", json={"username":"admin", "password":self.settings.password})
        self.assertEqual(result.status_code, 200)
        return result

    def report(self, **overrides):
        self.sequence += 1
        body = {"firmware_version":"2.1.0", "boot_id":"testboot01", "seq":self.sequence, "uptime_ms":self.sequence*2000,
                "mode":"auto", "pwm":[0,0,0], "ldr_raw":3000, "light_percent":73.26,
                "ina_valid":True, "voltage_v":5.0, "current_ma":0.0, "power_mw":0.0,
                "measurement_kind":"virtual_pwm_estimate", "ack_id":None}
        body.update(overrides)
        return self.client.post("/api/devices/streetlamp-01/sync", headers=self.auth, json=body)

    def command(self, mode="manual", pwm=None, profile="adaptive"):
        return self.client.post("/api/commands", json={"mode":mode, "pwm":pwm or [10,50,100],"auto_profile":profile})

    def state(self):
        return self.client.get("/api/state").json()

    def test_separate_authentication_and_no_token_in_dashboard(self):
        self.assertEqual(self.client.get("/api/state").status_code, 401)
        self.assertEqual(self.client.post("/api/devices/streetlamp-01/sync", json={}).status_code, 401)
        self.assertEqual(self.client.post("/api/login",json={"username":"admin","password":"bad"}).status_code,401)
        self.assertEqual(self.client.post("/api/login",json={"username":"admiñ","password":"bad"}).status_code,401)
        result = self.login()
        self.assertIn("HttpOnly", result.headers["set-cookie"])
        self.assertIn("SameSite=strict", result.headers["set-cookie"])
        self.assertNotIn(self.settings.device_token, self.client.get("/").text)
        self.assertNotIn(self.settings.device_token, self.client.get("/api/state").text)

    def test_command_delivered_then_confirmed_and_not_replayed(self):
        self.login()
        cmd = self.command().json()
        self.assertEqual(cmd["status"], "queued")
        delivered = self.report().json()["command"]
        self.assertEqual(delivered["id"],cmd["id"])
        self.assertEqual(self.state()["commands"][0]["status"],"delivered")
        applied = self.report(mode="manual",pwm=[10,50,100],ack_id=cmd["id"])
        self.assertIsNone(applied.json()["command"])
        self.assertEqual(self.state()["commands"][0]["status"],"applied")
        self.assertIsNone(self.report().json()["command"])

    def test_expiration_at_exactly_thirty_seconds(self):
        self.login()
        cmd = self.command().json()
        self.assertEqual(cmd["expires_at_ms"]-cmd["issued_at_ms"],30_000)
        self.now += 30_000
        self.assertIsNone(self.report().json()["command"])
        self.assertEqual(self.state()["commands"][0]["status"],"expired")

    def test_late_ack_cannot_claim_success(self):
        self.login(); cmd=self.command().json(); self.report()
        self.now+=30_000
        self.report(mode="manual",pwm=[10,50,100],ack_id=cmd["id"])
        self.assertEqual(self.state()["commands"][0]["status"],"expired")

    def test_confirmation_must_match_reported_pwm(self):
        self.login(); cmd=self.command().json(); self.report()
        self.report(mode="manual",pwm=[0,0,0],ack_id=cmd["id"])
        self.assertEqual(self.state()["commands"][0]["status"],"failed")

    def test_offline_at_fifteen_seconds_keeps_timestamp_and_last_data(self):
        self.login(); self.report(); seen=self.now
        self.now+=14_999; self.assertTrue(self.state()["online"])
        self.now+=1; state=self.state()
        self.assertFalse(state["online"]); self.assertEqual(state["last_seen_ms"],seen)
        self.assertIsNotNone(state["telemetry"])

    def test_newer_command_supersedes_pending_and_unique_same_millisecond(self):
        self.login(); a=self.command().json(); b=self.command(pwm=[0,20,80]).json()
        self.assertNotEqual(a["id"],b["id"])
        self.assertGreater(b["issued_at_ms"],a["issued_at_ms"])
        self.assertEqual(self.state()["commands"][1]["status"],"superseded")
        self.assertEqual(self.report().json()["command"]["id"],b["id"])

    def test_invalid_pwm_and_mode_are_rejected(self):
        self.login()
        for pwm in ([0,101,0],[-1,0,0],[1,2],[True,1,2],[1.2,2,3]):
            self.assertEqual(self.client.post("/api/commands",json={"mode":"manual","pwm":pwm}).status_code,422)
        self.assertEqual(self.command(mode="unknown").status_code,422)

    def test_duplicate_telemetry_does_not_duplicate_history_or_extend_online(self):
        self.login(); self.report(seq=10)
        self.now+=1000
        self.assertTrue(self.report(seq=10).json()["duplicate"])
        self.assertEqual(len(self.client.get("/api/history").json()),1)
        self.assertEqual(self.state()["last_seen_ms"],self.now-1000)
        self.assertEqual(self.report(seq=9).status_code,409)

    def test_restart_and_retired_boot_are_handled(self):
        self.login(); self.report(seq=100)
        self.assertEqual(self.report(boot_id="testboot02",seq=0).status_code,200)
        self.assertEqual(self.report(boot_id="testboot01",seq=101).status_code,409)

    def test_sensor_failure_is_null_and_history_has_real_gap(self):
        self.login(); self.report()
        self.now+=40_000
        self.assertEqual(self.report(ina_valid=False,voltage_v=None,current_ma=None,power_mw=None).status_code,200)
        rows=self.client.get("/api/history").json()
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[1]["received_at_ms"]-rows[0]["received_at_ms"],40_000)
        self.assertIsNone(rows[-1]["voltage_v"])
        self.assertEqual(self.report(ina_valid=False).status_code,422)

    def test_telemetry_backfill_preserves_chronological_state_and_history(self):
        self.login()
        newest_time = self.now
        self.assertEqual(self.report().status_code, 200)
        self.now += 2000
        self.assertEqual(self.report().status_code, 200)
        older_time = newest_time - 3000
        with Database(self.settings.database).session() as session:
            session.execute("UPDATE telemetry SET received_at_ms=%s WHERE device_id=%s AND seq=%s",
                            (older_time, self.settings.device_id, 2))

        self.assertEqual(self.state()["telemetry"]["seq"], 1)
        history = self.client.get("/api/history").json()
        self.assertEqual([row["received_at_ms"] for row in history], [older_time, newest_time])
        limited = self.client.get("/api/history", params={"limit": 1}).json()
        self.assertEqual([row["seq"] for row in limited], [1])

    def test_physical_ina219_readings_are_saved_with_their_source(self):
        self.login()
        self.assertEqual(self.report().status_code, 200)
        simulated = self.state()
        self.assertEqual(simulated["measurement_notice"], "PWM-based estimate")

        readings = {"voltage_v": 4.688, "current_ma": 2.00, "power_mw": 10.00}
        payload = {
            **simulated["telemetry"],
            **readings,
            "firmware_version": "2.3.0",
            "seq": self.sequence + 1,
            "measurement_kind": "ina219_measured",
        }
        with self.client.websocket_connect("/api/devices/streetlamp-01/live", headers=self.auth) as device:
            device.send_json(payload)
            self.assertFalse(device.receive_json()["duplicate"])

        state = self.state()
        self.assertEqual(state["measurement_notice"], "Measured by INA219")
        self.assertEqual(state["telemetry"]["measurement_kind"], "ina219_measured")
        for field, value in readings.items():
            self.assertEqual(state["telemetry"][field], value)

        rows = self.client.get("/api/history").json()
        self.assertEqual([row["measurement_kind"] for row in rows],
                         ["virtual_pwm_estimate", "ina219_measured"])
        for field, value in readings.items():
            self.assertEqual(rows[-1][field], value)

    def test_cross_origin_rejected_and_logout_revokes_browser_cookie(self):
        self.login()
        self.assertEqual(self.client.post("/api/commands",headers={"Origin":"http://elsewhere.test"},
                         json={"mode":"auto","pwm":[0,0,0]}).status_code,403)
        self.client.post("/api/logout")
        self.assertEqual(self.client.get("/api/state").status_code,401)

    def test_mysql_persists_across_app_restart(self):
        self.login(); cmd=self.command().json(); self.report()
        second=TestClient(create_app(self.settings)); second.app.state.clock=lambda:self.now
        second.post("/api/login",json={"username":"admin","password":self.settings.password})
        saved=second.get("/api/state").json()
        self.assertEqual(saved["commands"][0]["id"],cmd["id"])
        self.assertEqual(len(second.get("/api/history").json()),1)
        second.close()

    def test_light_test_profile_requires_matching_firmware_confirmation(self):
        self.login(); command=self.command(mode="auto",profile="light_only").json()
        self.report()
        self.report(mode="auto",auto_profile="light_only",pwm=[100,100,100],
                    light_condition="dark",light_percent=10,ack_id=command["id"])
        self.assertEqual(self.state()["commands"][0]["status"],"applied")
        self.assertEqual(self.state()["telemetry"]["auto_profile"],"light_only")

    def test_wrong_auto_profile_does_not_claim_light_test_applied(self):
        self.login(); command=self.command(mode="auto",profile="light_only").json();self.report()
        self.report(mode="auto",auto_profile="adaptive",ack_id=command["id"])
        self.assertEqual(self.state()["commands"][0]["status"],"failed")

    def test_vehicle_sensor_telemetry_is_saved_in_mysql(self):
        self.login()
        self.assertEqual(self.report(distance_cm=[90],vehicle_present=[True],
                                     vehicle_hold_ms=[5000],pwm=[100,100,100],light_condition="dark").status_code,200)
        telemetry=self.state()["telemetry"]
        self.assertEqual(telemetry["distance_cm"],[90])
        self.assertEqual(telemetry["pwm"],[100,100,100])
        self.assertEqual(self.state()["database"],"mysql")

    def test_invalid_sensor_arrays_rejected(self):
        self.assertEqual(self.report(distance_cm=[10,20]).status_code,422)
        self.assertEqual(self.report(distance_cm=[-1]).status_code,422)
        self.assertEqual(self.report(vehicle_hold_ms=[5001]).status_code,422)
        self.assertEqual(self.report(echo_pulse_us=[-1]).status_code,422)
        self.login()
        self.assertEqual(self.command(mode="auto",profile="unknown").status_code,422)

    def test_old_firmware_cannot_confirm_new_features(self):
        self.login(); command=self.command(mode="auto").json(); self.report(firmware_version="legacy")
        self.report(firmware_version="legacy",ack_id=command["id"])
        self.assertEqual(self.state()["commands"][0]["status"],"failed")

    def test_server_boot_ids_are_unique_and_restart_accepts_sequence_zero(self):
        url="/api/devices/streetlamp-01/boot"
        self.assertEqual(self.client.post(url).status_code,401)
        first=self.client.post(url,headers=self.auth).json()["boot_id"]
        second=self.client.post(url,headers=self.auth).json()["boot_id"]
        self.assertEqual(len(first),32);self.assertNotEqual(first,second)
        self.assertEqual(self.report(boot_id=first,seq=0).status_code,200)
        self.assertEqual(self.report(boot_id=second,seq=0).status_code,200)
        self.assertEqual(self.report(boot_id=first,seq=1).status_code,409)

    def test_concurrent_commands_have_unique_order_and_one_pending(self):
        self.login()
        with ThreadPoolExecutor(max_workers=5) as pool:
            responses=list(pool.map(lambda _:self.command(),range(10)))
        self.assertTrue(all(result.status_code==201 for result in responses))
        commands=[result.json() for result in responses]
        self.assertEqual(len({c["issued_at_ms"] for c in commands}),10)
        states=self.state()["commands"]
        self.assertEqual(sum(c["status"]=="queued" for c in states),1)

    def test_environment_command_requires_new_firmware_and_confirmed_bridge(self):
        self.login()
        cmd=self.client.post('/api/commands',json={'mode':'auto','pwm':[0,0,0],'auto_profile':'adaptive','environment':'night'}).json()
        self.assertEqual(cmd['environment'],'night')
        self.report()
        self.report(firmware_version='2.2.0',environment='night',environment_ready=True,mode='auto',pwm=[20,20,20],ack_id=cmd['id'])
        self.assertEqual(self.state()['commands'][0]['status'],'applied')

    def test_environment_mismatch_and_missing_bridge_fail_confirmation(self):
        self.login()
        for env,ready in [('morning',True),('night',False)]:
            cmd=self.client.post('/api/commands',json={'mode':'auto','pwm':[0,0,0],'environment':'night'}).json()
            self.report()
            self.report(firmware_version='2.2.0',environment=env,environment_ready=ready,ack_id=cmd['id'])
            self.assertEqual(self.state()['commands'][0]['status'],'failed')

    def test_invalid_environment_rejected_and_ordinary_command_preserves_preset(self):
        self.login()
        self.assertEqual(self.client.post('/api/commands',json={'mode':'auto','pwm':[0,0,0],'environment':'typhoon'}).status_code,422)
        self.assertIsNone(self.command().json()['environment'])

    def test_no_diagrams_in_dashboard_or_public_mount(self):
        self.login()
        self.assertNotIn('Diagram sistem',self.client.get('/').text)
        self.assertEqual(self.client.get('/diagrams/dfd.svg').status_code,404)
