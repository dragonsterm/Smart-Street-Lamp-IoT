#include <Arduino.h>
#include <Wire.h>
#include <Adafruit_INA219.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <WebSocketsClient.h>
#include <ArduinoJson.h>
#include <atomic>

// ================= ISI KONFIGURASI INI =================
const char WIFI_SSID[] = "--";
const char WIFI_PASSWORD[] = "--";
#define SERVER_IP "--"  // Ganti PASSWORD_WIFI_ANDAdengan IPv4 komputer server
constexpr uint16_t SERVER_PORT = 8000;
#define DEVICE_ID "streetlamp-01"
#define DEVICE_TOKEN "--"
#define DEVICE_PATH "/api/devices/" DEVICE_ID

// ================= PIN RANGKAIAN FISIK =================
constexpr uint8_t LDR_PIN = 34;
constexpr uint8_t TRIG_PIN = 19;
constexpr uint8_t ECHO_PIN = 18;
constexpr uint8_t SDA_PIN = 22;
constexpr uint8_t SCL_PIN = 21;
constexpr uint8_t LED_PINS[] = {25, 26, 27};

// Ubah ke true hanya jika ADC naik ketika LDR ditutup.
constexpr bool LDR_INVERTED = true;
constexpr uint32_t HOLD_MS = 10000;
constexpr uint32_t FADE_MS = 10000;
constexpr uint32_t FAILSAFE_MS = 30000;

struct Reading {
  uint16_t adc;
  float light;
  bool dark;
  float distance;
  uint32_t pulse;
  bool vehicle;
  uint32_t hold;
  bool manual;
  bool lightOnly;
  uint8_t pwm[3];
  bool inaValid;
  float busVoltage;
  float shuntVoltage;
  float currentMa;
  float powerMw;
  char ack[37];
};

struct Command {
  int64_t issued;
  uint32_t deadline;
  bool manual;
  bool lightOnly;
  uint8_t pwm[3];
  char id[37];
};

Reading reading{};
Adafruit_INA219 ina219;
bool inaReady;
QueueHandle_t readingsQueue;
QueueHandle_t commandsQueue;
std::atomic<uint32_t> lastContact{0};
std::atomic<int> bootStatus{0};

String makeTelemetry(const Reading &data, const char *bootId, uint32_t seq);

String makeTelemetry(const Reading &data, const char *bootId, uint32_t seq) {
  JsonDocument doc;
  doc["firmware_version"] = "2.3.0";
  doc["boot_id"] = bootId;
  doc["seq"] = seq;
  doc["uptime_ms"] = millis();
  doc["mode"] = data.manual ? "manual" : "auto";
  doc["ldr_raw"] = data.adc;
  doc["light_percent"] = data.light;
  doc["light_condition"] = data.dark ? "dark" : "bright";
  doc["auto_profile"] = data.lightOnly ? "light_only" : "adaptive";
  JsonArray distance = doc["distance_cm"].to<JsonArray>();
  if (data.distance >= 0) distance.add(data.distance);
  else distance.add(nullptr);
  doc["echo_pulse_us"].to<JsonArray>().add(data.pulse);
  doc["vehicle_present"].to<JsonArray>().add(data.vehicle);
  doc["vehicle_hold_ms"].to<JsonArray>().add(data.hold);
  JsonArray pwm = doc["pwm"].to<JsonArray>();
  for (uint8_t value : data.pwm) pwm.add(value);
  if (data.ack[0]) doc["ack_id"] = data.ack;

  doc["measurement_kind"] = "ina219_measured";
  doc["ina_valid"] = data.inaValid;
  if (data.inaValid) {
    doc["voltage_v"] = data.busVoltage;
    doc["current_ma"] = data.currentMa;
    doc["power_mw"] = data.powerMw;
  }

  String body;
  serializeJson(doc, body);
  return body;
}

void onSocketEvent(WStype_t event, uint8_t *payload, size_t length) {
  if (event != WStype_TEXT) return;
  JsonDocument doc;
  if (deserializeJson(doc, payload, length) ||
      strcmp(doc["device_id"] | "", DEVICE_ID) != 0 ||
      !doc["server_time_ms"].is<int64_t>()) return;

  uint32_t now = millis();
  lastContact.store(now);
  JsonObjectConst cmd = doc["command"].as<JsonObjectConst>();
  if (cmd.isNull() || !cmd["environment"].isNull() ||
      !cmd["issued_at_ms"].is<int64_t>() ||
      !cmd["expires_at_ms"].is<int64_t>()) return;

  const char *id = cmd["id"] | "";
  const char *mode = cmd["mode"] | "";
  const char *profile = cmd["auto_profile"] | "";
  int64_t ttl = cmd["expires_at_ms"].as<int64_t>() -
                doc["server_time_ms"].as<int64_t>();
  JsonArrayConst pwm = cmd["pwm"].as<JsonArrayConst>();
  if (strlen(id) != 36 || ttl <= 0 || ttl > 30000 || pwm.size() != 3 ||
      (strcmp(mode, "auto") && strcmp(mode, "manual")) ||
      (strcmp(profile, "adaptive") && strcmp(profile, "light_only"))) return;

  Command next{};
  next.issued = cmd["issued_at_ms"].as<int64_t>();
  next.deadline = now + static_cast<uint32_t>(ttl);
  next.manual = strcmp(mode, "manual") == 0;
  next.lightOnly = strcmp(profile, "light_only") == 0;
  for (uint8_t i = 0; i < 3; ++i) {
    if (!pwm[i].is<int>()) return;
    int value = pwm[i].as<int>();
    if (value < 0 || value > 100) return;
    next.pwm[i] = static_cast<uint8_t>(value);
  }
  strlcpy(next.id, id, sizeof(next.id));
  xQueueOverwrite(commandsQueue, &next);
}

void networkTask(void *) {
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  WebSocketsClient socket;
  socket.begin(SERVER_IP, SERVER_PORT, DEVICE_PATH "/live");
  socket.setExtraHeaders("Authorization: Bearer " DEVICE_TOKEN "\r\n");
  socket.onEvent(onSocketEvent);
  socket.setReconnectInterval(1000);
  socket.enableHeartbeat(5000, 2000, 2);

  const String bootUrl = String("http://") + SERVER_IP + ":" +
                         SERVER_PORT + DEVICE_PATH "/boot";
  char bootId[33] = {};
  uint32_t seq = 0;
  uint32_t lastSent = millis() - 1000;
  uint32_t lastRetry = millis();
  for (;;) {
    if (WiFi.status() != WL_CONNECTED) {
      if (millis() - lastRetry >= 10000) {
        lastRetry = millis();
        WiFi.reconnect();
      }
      delay(100);
      continue;
    }
    if (!bootId[0]) {
      WiFiClient client;
      HTTPClient http;
      if (http.begin(client, bootUrl)) {
        http.setConnectTimeout(1500);
        http.setTimeout(1500);
        http.addHeader("Content-Type", "application/json");
        http.addHeader("Authorization", "Bearer " DEVICE_TOKEN);
        int status = http.POST("{}");
        bootStatus.store(status);
        JsonDocument doc;
        if (status == 200 && !deserializeJson(doc, http.getString())) {
          const char *id = doc["boot_id"] | "";
          if (strlen(id) == 32) strlcpy(bootId, id, sizeof(bootId));
        }
        http.end();
      }
      if (!bootId[0]) {
        delay(1500);
        continue;
      }
    }
    socket.loop();
    if (socket.isConnected() && millis() - lastSent >= 1000) {
      Reading data;
      if (xQueuePeek(readingsQueue, &data, 0) == pdTRUE) {
        String body = makeTelemetry(data, bootId, seq++);
        socket.sendTXT(body);
        lastSent = millis();
      }
    }
    delay(5);
  }
}

void readSensors() {
  reading.adc = analogRead(LDR_PIN);
  reading.light = (LDR_INVERTED ? 4095 - reading.adc : reading.adc) *
                  (100.0f / 4095.0f);
  if (reading.light <= 30.0f) reading.dark = true;
  else if (reading.light >= 45.0f) reading.dark = false;

  digitalWrite(TRIG_PIN, LOW);
  delayMicroseconds(2);
  digitalWrite(TRIG_PIN, HIGH);
  delayMicroseconds(10);
  digitalWrite(TRIG_PIN, LOW);
  reading.pulse = pulseIn(ECHO_PIN, HIGH, 30000);
  float cm = reading.pulse * 0.01715f;
  reading.distance = cm >= 2.0f && cm <= 400.0f ? cm : -1.0f;
  if (reading.distance < 0 || reading.distance >= 180.0f) reading.vehicle = false;
  else if (reading.distance <= 150.0f) reading.vehicle = true;
}

void updateLamps() {
  static int64_t lastIssued = 0;
  static bool wasAdaptive = false;
  static bool hadVehicle = false;
  static bool localLightTest = false;
  static uint32_t emptySince = 0;
  uint32_t contact = lastContact.load();
  uint32_t now = millis();

  while (Serial.available()) {
    int key = Serial.read();
    if (key == 'L' || key == 'l' || key == 'A' || key == 'a') {
      reading.manual = false;
      localLightTest = key == 'L' || key == 'l';
      reading.lightOnly = localLightTest;
      reading.ack[0] = '\0';
    }
  }

  Command cmd;
  if (commandsQueue && xQueueReceive(commandsQueue, &cmd, 0) == pdTRUE &&
      static_cast<int32_t>(cmd.deadline - now) > 0 && cmd.issued > lastIssued) {
    lastIssued = cmd.issued;
    localLightTest = false;
    reading.manual = cmd.manual;
    reading.lightOnly = cmd.lightOnly;
    memcpy(reading.pwm, cmd.pwm, sizeof(reading.pwm));
    strlcpy(reading.ack, cmd.id, sizeof(reading.ack));
    contact = lastContact.load();
    now = millis();
  }

  if (now - contact >= FAILSAFE_MS &&
      (reading.manual || (reading.lightOnly && !localLightTest))) {
    reading.manual = false;
    reading.lightOnly = false;
    reading.ack[0] = '\0';
  }

  bool adaptive = reading.dark && !reading.manual && !reading.lightOnly &&
                  reading.distance >= 0;
  if (adaptive && (!wasAdaptive || reading.vehicle || hadVehicle)) emptySince = now;
  wasAdaptive = adaptive;
  hadVehicle = reading.vehicle;
  uint32_t emptyMs = now - emptySince;
  reading.hold = adaptive && emptyMs < HOLD_MS ? HOLD_MS - emptyMs : 0;

  if (!reading.manual) {
    uint8_t duty = reading.dark ? 100 : 0;
    if (adaptive && !reading.vehicle && emptyMs > HOLD_MS) {
      uint32_t fade = min(emptyMs - HOLD_MS, FADE_MS);
      duty = 100 - fade * 50 / FADE_MS;
    }
    for (uint8_t &value : reading.pwm) value = duty;
  }
  for (uint8_t i = 0; i < 3; ++i) {
    analogWrite(LED_PINS[i], (reading.pwm[i] * 255 + 50) / 100);
  }
}

void readPower() {
  reading.inaValid = false;
  if (!inaReady) inaReady = ina219.begin(&Wire);
  if (!inaReady) return;

  reading.shuntVoltage = ina219.getShuntVoltage_mV();
  bool valid = ina219.success();
  reading.busVoltage = ina219.getBusVoltage_V();
  valid = ina219.success() && valid;
  reading.currentMa = ina219.getCurrent_mA();
  valid = ina219.success() && valid;
  reading.powerMw = ina219.getPower_mW();
  inaReady = ina219.success() && valid;
  reading.inaValid = inaReady &&
                     reading.busVoltage >= 0 && reading.busVoltage <= 32 &&
                     reading.currentMa >= -3200 && reading.currentMa <= 3200 &&
                     reading.powerMw >= 0 && reading.powerMw <= 100000;
}

void printMonitor() {
  const char *lightStatus = reading.dark ? "GELAP" :
                            reading.light < 73.26f ? "REDUP" : "TERANG";
  Serial.println("\n================================");
  Serial.println("       SENSOR LDR");
  Serial.printf("Nilai ADC       : %u\n", static_cast<unsigned>(reading.adc));
  Serial.printf("Kondisi Cahaya  : %s\n", lightStatus);

  Serial.println("\n       SENSOR HC-SR04");
  if (reading.distance < 0) Serial.println("Jarak           : Tidak ada respons Echo yang valid");
  else Serial.printf("Jarak           : %.2f cm\n", reading.distance);
  Serial.printf("Kendaraan       : %s\n", reading.distance < 0 ? "Tidak diketahui" :
                reading.vehicle ? "Terdeteksi" : "Tidak terdeteksi");

  Serial.println("\n       SENSOR INA219");
  if (reading.inaValid) {
    Serial.printf("Bus Voltage     : %.3f V\n", reading.busVoltage);
    Serial.printf("Shunt Voltage   : %.3f mV\n", reading.shuntVoltage);
    Serial.printf("Load Voltage    : %.3f V\n", reading.busVoltage);  // VIN- di sisi beban
    Serial.printf("Current         : %.2f mA\n", reading.currentMa);
    Serial.printf("Power           : %.2f mW\n", reading.powerMw);
  } else {
    Serial.println("INA219 tidak ditemukan atau pembacaan gagal!");
  }

  Serial.println("\n       LAMPU DAN SERVER");
  Serial.printf("Lampu 25/26/27  : %d%% / %d%% / %d%%\n",
                reading.pwm[0], reading.pwm[1], reading.pwm[2]);
  Serial.printf("Mode            : %s\n", reading.manual ? "MANUAL" :
                reading.lightOnly ? "UJI LDR" : "AUTO ADAPTIF");
  uint32_t contact = lastContact.load();
  int status = bootStatus.load();
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("Server          : Wi-Fi belum tersambung");
  } else if (contact != 0 && millis() - contact < 5000) {
    Serial.println("Server          : Dashboard terhubung");
  } else if (status != 0 && status != 200) {
    Serial.printf("Server          : Registrasi gagal (%d)\n", status);
  } else {
    Serial.println("Server          : Menunggu balasan dashboard");
  }
  Serial.println("================================");
}

void setup() {
  Serial.begin(115200);
  for (uint8_t pin : LED_PINS) {
    pinMode(pin, OUTPUT);
    digitalWrite(pin, LOW);
  }
  pinMode(LDR_PIN, INPUT);
  analogReadResolution(12);
  pinMode(TRIG_PIN, OUTPUT);
  digitalWrite(TRIG_PIN, LOW);
  pinMode(ECHO_PIN, INPUT);
  Wire.begin(SDA_PIN, SCL_PIN);
  Wire.setTimeOut(30);
  inaReady = ina219.begin(&Wire);

  Serial.println("==============================");
  Serial.println(" ESP32 SENSOR MONITORING");
  Serial.println("==============================");
  Serial.println("LDR       : READY");
  Serial.println("HC-SR04   : READY");
  Serial.println(inaReady ? "INA219    : READY" : "INA219 tidak ditemukan!");
  Serial.println("Ketik L untuk Uji LDR atau A untuk Auto Adaptif.");

  readingsQueue = xQueueCreate(1, sizeof(Reading));
  commandsQueue = xQueueCreate(1, sizeof(Command));
  if (!readingsQueue || !commandsQueue ||
      xTaskCreate(networkTask, "dashboard", 12288, nullptr, 1, nullptr) != pdPASS) {
    Serial.println("Koneksi server gagal dimulai. Kontrol lokal tetap aktif.");
  }
}

void loop() {
  static uint32_t lastSample = millis() - 100;
  static uint32_t lastPrint = millis() - 1000;
  uint32_t now = millis();
  bool sampleDue = now - lastSample >= 100;
  bool monitorDue = now - lastPrint >= 1000;
  if (sampleDue) {
    lastSample = now;
    readSensors();
    updateLamps();
  }
  if (monitorDue) {
    lastPrint = now;
    readPower();
  }
  if ((sampleDue || monitorDue) && readingsQueue) {
    xQueueOverwrite(readingsQueue, &reading);
  }
  if (monitorDue) printMonitor();
  delay(5);
}
