#include <WiFi.h>
#include <PubSubClient.h>
#include <DHTesp.h>
#include <sys/time.h>
#include <time.h>
#include <esp_system.h>

const char* MQTT_HOST = "broker.hivemq.com";
const int MQTT_PORT = 1883;
const char* MQTT_TOPIC = "ptit/iot2/B23DCCN140/dat-a73f/telemetry";
const char* DEVICE_ID = "esp32_01";

const int DHT_PIN = 15;
const unsigned long SEND_INTERVAL = 5000;

WiFiClient wifiClient;
PubSubClient mqtt(wifiClient);
DHTesp dht;

char bootId[17];
String clientId;
unsigned long sequenceNumber = 0;
unsigned long lastSend = 0;
unsigned long lastMqttAttempt = 0;

void connectWiFi() {
  Serial.print("Connecting WiFi");
  WiFi.begin("Wokwi-GUEST", "", 6);

  unsigned long started = millis();

  while (WiFi.status() != WL_CONNECTED) {
    delay(250);
    Serial.print(".");

    if (millis() - started > 20000) {
      Serial.println("\nWiFi timeout. Restarting...");
      ESP.restart();
    }
  }

  Serial.println("\nWiFi connected");
  Serial.print("IP: ");
  Serial.println(WiFi.localIP());
}

void syncTime() {
  configTime(0, 0, "pool.ntp.org", "time.google.com");
  Serial.print("Syncing time");

  unsigned long started = millis();

  while (time(nullptr) < 1700000000) {
    delay(250);
    Serial.print(".");

    if (millis() - started > 20000) {
      Serial.println("\nTime sync pending; waiting before publishing");
      return;
    }
  }

  Serial.println("\nTime synchronized");
}

void connectMQTT() {
  Serial.print("Connecting MQTT... ");

  if (mqtt.connect(clientId.c_str())) {
    Serial.println("connected");
    Serial.print("Topic: ");
    Serial.println(MQTT_TOPIC);
  } else {
    Serial.print("failed, rc=");
    Serial.println(mqtt.state());
  }
}

void publishData() {
  if (time(nullptr) < 1700000000) {
    Serial.println("Waiting for valid time");
    return;
  }

  TempAndHumidity data = dht.getTempAndHumidity();

  if (isnan(data.temperature) || isnan(data.humidity)) {
    Serial.println("DHT read failed");
    return;
  }

  struct timeval tv;
  gettimeofday(&tv, nullptr);

  unsigned long long timestampMs =
      (unsigned long long)tv.tv_sec * 1000ULL + tv.tv_usec / 1000;

  sequenceNumber++;

  char payload[384];

  snprintf(
    payload,
    sizeof(payload),
    "{\"device_id\":\"%s\",\"boot_id\":\"%s\","
    "\"sequence\":%lu,\"timestamp_ms\":%llu,"
    "\"temperature\":%.2f,\"humidity\":%.2f}",
    DEVICE_ID,
    bootId,
    sequenceNumber,
    timestampMs,
    data.temperature,
    data.humidity
  );

  bool sent = mqtt.publish(MQTT_TOPIC, payload, false);

  Serial.print(sent ? "PUBLISH OK: " : "PUBLISH FAILED: ");
  Serial.println(payload);
}

void setup() {
  Serial.begin(115200);
  dht.setup(DHT_PIN, DHTesp::DHT22);

  connectWiFi();

  snprintf(
    bootId,
    sizeof(bootId),
    "%08lx%08lx",
    (unsigned long)esp_random(),
    (unsigned long)esp_random()
  );

  clientId = String("iot2-") + bootId;

  syncTime();

  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setBufferSize(512);
  mqtt.setKeepAlive(30);

  connectMQTT();
  lastMqttAttempt = millis();
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    connectWiFi();
  }

  if (!mqtt.connected()) {
    if (millis() - lastMqttAttempt >= 3000) {
      lastMqttAttempt = millis();
      connectMQTT();
    }

    delay(10);
    return;
  }

  mqtt.loop();

  if (millis() - lastSend >= SEND_INTERVAL) {
    lastSend = millis();
    publishData();
  }

  delay(10);
}