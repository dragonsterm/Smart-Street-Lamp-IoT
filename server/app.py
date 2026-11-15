"""Run from repo root: uvicorn server.app:create_app --factory."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
import pymysql
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.requests import HTTPConnection
from server.database import Database, MySQLConfig

ROOT = Path(__file__).resolve().parents[1]
OFFLINE_MS = 15_000
COMMAND_TTL_MS = 30_000
SESSION_SECONDS = 8 * 3600
Mode = Literal["auto", "manual"]
Percent = Annotated[int, Field(strict=True, ge=0, le=100)]
Pwm = Annotated[list[Percent], Field(min_length=3, max_length=3)]
Environment = Literal["sensor", "morning", "evening", "night", "storm", "bright", "dark"]
PRESET_FIRMWARE = ("2.2.0", "2.3.0")


def read_env(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


@dataclass
class Settings:
    database: MySQLConfig
    device_id: str
    device_token: str
    username: str
    password: str
    session_secret: str
    secure_cookie: bool = False

    @classmethod
    def load(cls):
        values = {**read_env(ROOT / ".env"), **os.environ}
        required = ("DEVICE_TOKEN", "SESSION_SECRET")
        if any(len(values.get(k, "")) < 20 or values[k].startswith("replace-") for k in required):
            raise RuntimeError("Configure local credentials first: python scripts/configure.py")
        if not values.get("DASHBOARD_PASSWORD"):
            raise RuntimeError("DASHBOARD_PASSWORD is required")
        database = MySQLConfig(host=values.get("MYSQL_HOST", "127.0.0.1"),
            port=int(values.get("MYSQL_PORT", "3306")), user=values.get("MYSQL_USER", "streetlamp"),
            password=values.get("MYSQL_PASSWORD", ""), database=values.get("MYSQL_DATABASE", "smart_street_lamp"),
            ssl_ca=values.get("MYSQL_SSL_CA") or None)
        return cls(database,
                   values.get("DEVICE_ID", "streetlamp-01"), values["DEVICE_TOKEN"],
                   values.get("DASHBOARD_USERNAME", "admin"), values["DASHBOARD_PASSWORD"],
                   values["SESSION_SECRET"], values.get("COOKIE_SECURE", "false").lower() == "true")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Login(StrictModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=200)


class CommandInput(StrictModel):
    mode: Mode
    pwm: Pwm
    auto_profile: Literal["adaptive", "light_only"] = "adaptive"
    environment: Environment | None = None


class Telemetry(StrictModel):
    firmware_version: Literal["legacy", "2.0.0", "2.1.0", "2.2.0", "2.3.0"] = "legacy"
    boot_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{8,64}$")
    seq: int = Field(strict=True, ge=0, le=4_294_967_295)
    uptime_ms: int = Field(strict=True, ge=0, le=4_294_967_295)
    mode: Mode
    pwm: Pwm
    ldr_raw: int = Field(strict=True, ge=0, le=4095)
    ldr_digital_dark: bool = Field(default=False, strict=True)
    light_percent: float = Field(ge=0, le=100)
    light_condition: Literal["dark", "bright", "unknown"] = "unknown"
    auto_profile: Literal["adaptive", "light_only"] = "adaptive"
    environment: Environment = "sensor"
    environment_source: Literal["sensor", "dashboard", "wokwi"] = "sensor"
    environment_ready: bool = Field(default=False, strict=True)
    distance_cm: Annotated[list[Annotated[float | None, Field(ge=2, le=400)]], Field(min_length=1, max_length=1)] = [None]
    echo_pulse_us: Annotated[list[Annotated[int, Field(strict=True, ge=0, le=50000)]], Field(min_length=1, max_length=1)] = [0]
    vehicle_present: Annotated[list[bool], Field(min_length=1, max_length=1)] = [False]
    vehicle_hold_ms: Annotated[list[Annotated[int, Field(strict=True, ge=0, le=10000)]], Field(min_length=1, max_length=1)] = [0]
    ina_valid: bool = Field(strict=True)
    voltage_v: float | None = Field(default=None, ge=0, le=32)
    current_ma: float | None = Field(default=None, ge=-3200, le=3200)
    power_mw: float | None = Field(default=None, ge=0, le=100_000)
    measurement_kind: Literal["virtual_pwm_estimate", "ina219_measured"]
    ack_id: str | None = Field(default=None, pattern=r"^[0-9a-f-]{36}$")

    @model_validator(mode="before")
    @classmethod
    def legacy_three_sensor_history(cls, data):
        if isinstance(data,dict) and data.get("firmware_version") not in ("2.1.0", *PRESET_FIRMWARE):
            data=dict(data)
            for field in ("distance_cm","echo_pulse_us","vehicle_present","vehicle_hold_ms"):
                if isinstance(data.get(field),list) and len(data[field])==3:
                    data[field]=data[field][:1]
        return data

    @model_validator(mode="after")
    def sensor_consistency(self):
        if self.firmware_version != "2.3.0" and self.vehicle_hold_ms[0] > 5000:
            raise ValueError("Older firmware reports at most five seconds of hold")
        if "light_condition" not in self.model_fields_set:
            self.light_condition = "dark" if self.light_percent <= 30 else "bright" if self.light_percent >= 45 else "unknown"
        values = (self.voltage_v, self.current_ma, self.power_mw)
        if self.ina_valid != all(v is not None for v in values):
            raise ValueError("Valid INA needs all measurements; failed INA needs null measurements")
        if not self.ina_valid and any(v is not None for v in values):
            raise ValueError("Do not present stale INA readings as new data")
        return self


def confirms_command(report: Telemetry, command):
    if report.firmware_version not in ("2.1.0", *PRESET_FIRMWARE) or report.mode != command["mode"]:
        return False
    if report.mode == "manual" and report.pwm != json.loads(command["pwm"]):
        return False
    if report.mode == "auto" and report.auto_profile != command["auto_profile"]:
        return False
    environment = command["environment"]
    if environment is None:
        return True
    if not report.environment_ready or report.firmware_version not in PRESET_FIRMWARE:
        return False
    if report.firmware_version == "2.2.0":
        return report.environment == environment
    if environment == "sensor":
        return report.environment_source == "wokwi"
    return report.environment_source == "dashboard" and report.environment == environment


async def run_socket(reader, writer):
    tasks = [asyncio.create_task(reader), asyncio.create_task(writer)]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.load()
    database = Database(settings.database)
    database.initialize(settings.device_id)
    app = FastAPI(title="Smart Street Lamp", docs_url=None, redoc_url=None)
    app.state.settings = settings
    app.state.clock = lambda: int(time.time() * 1000)
    dashboard_listeners: set[asyncio.Queue] = set()
    device_listeners: set[asyncio.Queue] = set()
    # Derive once at startup; no clear-text password leaves the server.
    salt = hashlib.sha256(settings.session_secret.encode()).digest()
    password_hash = hashlib.pbkdf2_hmac("sha256", settings.password.encode(), salt, 150_000)

    def db(write=False):
        return database.session(write, settings.device_id)

    @app.exception_handler(pymysql.MySQLError)
    async def database_unavailable(request, error):
        return JSONResponse(status_code=503, content={"detail":"Database MySQL tidak tersedia"})

    def clock():
        return app.state.clock()

    def check_origin(request: HTTPConnection):
        origin = request.headers.get("origin")
        base = request.base_url
        if base.scheme in ("ws", "wss"):
            base = base.replace(scheme="https" if base.scheme == "wss" else "http")
        if origin and origin.rstrip("/") != str(base).rstrip("/"):
            raise HTTPException(403, "Cross-origin request refused")

    def sign(value: str):
        return hmac.new(settings.session_secret.encode(), value.encode(), hashlib.sha256).hexdigest()

    def user(request: HTTPConnection):
        check_origin(request)
        cookie = request.cookies.get("lamp_session", "")
        try:
            value, signature = cookie.rsplit(".", 1)
            username, expiry, _ = value.split(":", 2)
            valid = (hmac.compare_digest(signature, sign(value)) and
                     hmac.compare_digest(username, settings.username) and int(expiry) > clock() // 1000)
        except (ValueError, TypeError):
            valid = False
        if not valid:
            raise HTTPException(401, "Sign in required")

    def device(request: HTTPConnection, device_id: str):
        if device_id != settings.device_id or not hmac.compare_digest(
                request.headers.get("authorization", "").encode(), ("Bearer " + settings.device_token).encode()):
            raise HTTPException(401, "Invalid device credentials")

    def expire(connection, now):
        return connection.execute("""UPDATE commands SET status='expired', detail='No timely confirmation'
            WHERE status IN ('queued','delivered') AND expires_at_ms<=%s""", (now,)).rowcount > 0

    def command_dict(row):
        if row is None:
            return None
        result = dict(row)
        result["pwm"] = json.loads(result["pwm"])
        return result

    def recent_commands(connection):
        rows = connection.execute("""SELECT * FROM commands WHERE device_id=%s
            ORDER BY issued_at_ms DESC LIMIT 20""", (settings.device_id,)).fetchall()
        return [command_dict(row) for row in rows]

    def take_pending(connection, device_id, now):
        pending = connection.execute("""SELECT * FROM commands WHERE device_id=%s
            AND status IN ('queued','delivered') AND expires_at_ms>%s
            ORDER BY issued_at_ms DESC LIMIT 1""", (device_id, now)).fetchone()
        changed = pending is not None and pending["status"] == "queued"
        if changed:
            connection.execute("""UPDATE commands SET status='delivered', delivered_at_ms=%s
                WHERE id=%s""", (now, pending["id"]))
            pending["status"] = "delivered"
            pending["delivered_at_ms"] = now
        return command_dict(pending), changed

    def publish(update):
        if not update:
            return
        for queue in dashboard_listeners:
            if queue.full():
                # Slow browsers resynchronize from a fresh snapshot.
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait(None)
            else:
                queue.put_nowait(update)

    @app.middleware("http")
    async def headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        return response

    @app.get("/health")
    def health():
        with db() as connection:
            connection.execute("SELECT 1").fetchone()
        return {"status": "ok", "database": "mysql"}

    @app.post("/api/login")
    def login(body: Login, request: Request, response: Response):
        check_origin(request)
        candidate = hashlib.pbkdf2_hmac("sha256", body.password.encode(), salt, 150_000)
        if not (hmac.compare_digest(body.username.encode(), settings.username.encode()) and
                hmac.compare_digest(candidate, password_hash)):
            raise HTTPException(401, "Username or password incorrect")
        value = f"{settings.username}:{clock() // 1000 + SESSION_SECONDS}:{secrets.token_hex(12)}"
        response.set_cookie("lamp_session", f"{value}.{sign(value)}", httponly=True,
                            secure=settings.secure_cookie, samesite="strict", max_age=SESSION_SECONDS)
        return {"username": settings.username}

    @app.post("/api/logout")
    def logout(request: Request, response: Response):
        check_origin(request)
        response.delete_cookie("lamp_session", secure=settings.secure_cookie, samesite="strict")
        return {"ok": True}

    @app.get("/api/state", dependencies=[Depends(user)])
    def state():
        now = clock()
        with db(True) as connection:
            expire(connection, now)
            row = connection.execute("SELECT * FROM devices WHERE id=%s", (settings.device_id,)).fetchone()
            latest = connection.execute("SELECT * FROM telemetry WHERE device_id=%s ORDER BY received_at_ms DESC, id DESC LIMIT 1",
                                        (settings.device_id,)).fetchone()
            commands = recent_commands(connection)
        seen = row["last_seen_ms"]
        telemetry = Telemetry.model_validate_json(latest["payload"]) if latest else None
        measurement_notice = "INA219 reading unavailable"
        if telemetry and telemetry.ina_valid:
            measurement_notice = ("Measured by INA219" if telemetry.measurement_kind == "ina219_measured"
                                  else "PWM-based estimate")
        return {"device_id": settings.device_id, "server_time_ms": now, "last_seen_ms": seen,
                "online": seen is not None and now - seen < OFFLINE_MS,
                "telemetry": telemetry.model_dump() if telemetry else None,
                "commands": commands,
                "database": "mysql", "control_rules": {
                    "dim_percent": 50, "bright_percent": 100, "vehicle_enter_cm": 150,
                    "vehicle_exit_cm": 180, "hold_ms": 10000, "fade_ms": 10000},
                "measurement_notice": measurement_notice}

    @app.get("/api/history", dependencies=[Depends(user)])
    def history(limit: int = 300):
        if not 1 <= limit <= 1200:
            raise HTTPException(422, "limit must be 1..1200")
        with db() as connection:
            rows = connection.execute("""SELECT received_at_ms,payload FROM telemetry
                WHERE device_id=%s ORDER BY received_at_ms DESC, id DESC LIMIT %s""", (settings.device_id, limit)).fetchall()
        return [{"received_at_ms": row["received_at_ms"], **json.loads(row["payload"])} for row in reversed(rows)]

    def issue_command(body: CommandInput, connected: bool):
        import uuid
        with db(True) as connection:
            now = clock()
            expire(connection, now)
            previous = connection.execute("SELECT MAX(issued_at_ms) AS latest FROM commands").fetchone()["latest"]
            issued = max(now, (previous or 0) + 1)
            connection.execute("""UPDATE commands SET status='superseded', detail='Replaced by a newer command'
                WHERE device_id=%s AND status IN ('queued','delivered')""", (settings.device_id,))
            connection.execute("""INSERT INTO commands
                (id,device_id,issued_at_ms,expires_at_ms,mode,pwm,auto_profile,environment,status,delivered_at_ms)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (str(uuid.uuid4()), settings.device_id, issued, now + COMMAND_TTL_MS,
                 body.mode, json.dumps(body.pwm), body.auto_profile, body.environment,
                 "delivered" if connected else "queued", now if connected else None))
            commands = recent_commands(connection)
        return commands, now

    @app.post("/api/commands", status_code=201, dependencies=[Depends(user)])
    async def command(body: CommandInput):
        connected = bool(device_listeners)
        commands, now = await run_in_threadpool(issue_command, body, connected)
        publish({"type": "update", "server_time_ms": now, "commands": commands})
        for queue in device_listeners:
            # A live socket can send this committed row immediately. A socket
            # that connected during issuance loads any queued row from MySQL.
            pending = commands[0] if connected else None
            if queue.full():
                previous = queue.get_nowait()
                if previous and pending and previous["issued_at_ms"] > pending["issued_at_ms"]:
                    pending = previous
            queue.put_nowait(pending)
        return commands[0]

    def ingest(device_id: str, body: Telemetry, poll_for_command: bool):
        with db(True) as connection:
            now = clock()
            commands_changed = expire(connection, now)
            boot = connection.execute("SELECT last_seq FROM boots WHERE device_id=%s AND boot_id=%s",
                                      (device_id, body.boot_id)).fetchone()
            active = connection.execute("SELECT active_boot FROM devices WHERE id=%s", (device_id,)).fetchone()["active_boot"]
            duplicate = boot is not None and body.seq == boot["last_seq"]
            if boot and (active != body.boot_id or body.seq < boot["last_seq"]):
                raise HTTPException(409, "Stale telemetry sequence or retired boot")
            if not duplicate:
                connection.execute("INSERT INTO telemetry(device_id,boot_id,seq,received_at_ms,payload) VALUES(%s,%s,%s,%s,%s)",
                                   (device_id, body.boot_id, body.seq, now, body.model_dump_json()))
                connection.execute("""INSERT INTO boots VALUES(%s,%s,%s)
                    ON DUPLICATE KEY UPDATE last_seq=VALUES(last_seq)""", (device_id, body.boot_id, body.seq))
                connection.execute("UPDATE devices SET last_seen_ms=%s,active_boot=%s WHERE id=%s",
                                   (now, body.boot_id, device_id))
                if body.ack_id:
                    acknowledged = connection.execute("""SELECT * FROM commands WHERE id=%s AND device_id=%s
                        AND status='delivered'""", (body.ack_id, device_id)).fetchone()
                    if acknowledged:
                        matches = confirms_command(body, acknowledged)
                        connection.execute("UPDATE commands SET status=%s,applied_at_ms=%s,detail=%s WHERE id=%s",
                            ("applied" if matches else "failed", now if matches else None,
                             "Confirmed by device" if matches else "Reported state differs from command", body.ack_id))
                        commands_changed = True
            pending = None
            if poll_for_command:
                pending, delivered = take_pending(connection, device_id, now)
                commands_changed = commands_changed or delivered
            update = {"type": "update", "server_time_ms": now}
            if not duplicate:
                update.update(last_seen_ms=now, telemetry=body.model_dump())
            if commands_changed:
                update["commands"] = recent_commands(connection)
        reply = {"device_id": device_id, "server_time_ms": now, "duplicate": duplicate}
        if poll_for_command:
            reply["command"] = pending
        return reply, update if not duplicate or commands_changed else None

    @app.post("/api/devices/{device_id}/sync", dependencies=[Depends(device)])
    async def sync(device_id: str, body: Telemetry):
        reply, update = await run_in_threadpool(ingest, device_id, body, True)
        publish(update)
        return reply

    def pending_for_socket(device_id):
        with db(True) as connection:
            now = clock()
            expired = expire(connection, now)
            pending, delivered = take_pending(connection, device_id, now)
            commands = recent_commands(connection) if expired or delivered else None
        return pending, commands, now

    @app.websocket("/api/devices/{device_id}/live")
    async def device_live(socket: WebSocket, device_id: str):
        try:
            device(socket, device_id)
        except HTTPException:
            await socket.close(code=1008)
            return
        await socket.accept()
        queue = asyncio.Queue(maxsize=1)
        queue.put_nowait(None)
        device_listeners.add(queue)
        send_lock = asyncio.Lock()

        async def receive_reports():
            while True:
                raw = await socket.receive_text()
                if len(raw) > 4096:
                    await socket.close(code=1009)
                    return
                try:
                    body = Telemetry.model_validate_json(raw)
                    reply, update = await run_in_threadpool(ingest, device_id, body, False)
                except (ValidationError, HTTPException):
                    await socket.close(code=1008)
                    return
                publish(update)
                async with send_lock:
                    await socket.send_json(reply)

        async def send_commands():
            last_issued = 0
            while True:
                pending = await queue.get()
                if pending is None:
                    pending, commands, now = await run_in_threadpool(pending_for_socket, device_id)
                    if commands is not None:
                        publish({"type": "update", "server_time_ms": now, "commands": commands})
                if pending and pending["issued_at_ms"] > last_issued:
                    async with send_lock:
                        now = clock()
                        if pending["expires_at_ms"] > now:
                            await socket.send_json({
                                "device_id": device_id, "server_time_ms": now, "command": pending})
                            last_issued = pending["issued_at_ms"]

        try:
            await run_socket(receive_reports(), send_commands())
        except WebSocketDisconnect:
            pass
        except pymysql.MySQLError:
            await socket.close(code=1011)
        finally:
            device_listeners.discard(queue)

    @app.websocket("/api/live")
    async def dashboard_live(socket: WebSocket):
        try:
            user(socket)
        except HTTPException:
            await socket.close(code=1008)
            return
        await socket.accept()
        queue = asyncio.Queue(maxsize=32)
        dashboard_listeners.add(queue)

        async def send_updates():
            await socket.send_json({"type": "snapshot", "state": await run_in_threadpool(state)})
            while True:
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=2)
                except asyncio.TimeoutError:
                    message = {"type": "heartbeat", "server_time_ms": clock()}
                user(socket)
                if message is None:
                    message = {"type": "snapshot", "state": await run_in_threadpool(state)}
                await socket.send_json(message)

        try:
            await run_socket(socket.receive_text(), send_updates())
            await socket.close(code=1008)
        except WebSocketDisconnect:
            pass
        except HTTPException:
            await socket.close(code=1008)
        except pymysql.MySQLError:
            await socket.close(code=1011)
        finally:
            dashboard_listeners.discard(queue)


    @app.post("/api/devices/{device_id}/boot", dependencies=[Depends(device)])
    def boot_session(device_id: str):
        # Allocate a fresh boot ID so sequence numbers can restart safely.
        return {"device_id":device_id, "boot_id":secrets.token_hex(16), "server_time_ms":clock()}

    static = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.get("/")
    def index():
        return FileResponse(static / "index.html")

    return app


# A factory keeps tests independent of the user's local credentials/database.
# uvicorn --factory server.app:create_app is the documented entry point.
