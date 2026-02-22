#include <Arduino.h>

static constexpr uint8_t GPIO7_PIN = 7;
static constexpr uint8_t GPIO8_PIN = 8;

void setup()
{
  Serial.begin(115200);

  pinMode(GPIO7_PIN, OUTPUT);
  pinMode(GPIO8_PIN, OUTPUT);

  digitalWrite(GPIO7_PIN, LOW);
  digitalWrite(GPIO8_PIN, LOW);
}

void loop()
{
  static uint8_t state = 0;

  // GPIO7 = LSB, GPIO8 = MSB -> binary 0..3
  digitalWrite(GPIO7_PIN, (state & 1) ? HIGH : LOW);
  digitalWrite(GPIO8_PIN, (state & 2) ? HIGH : LOW);

  Serial.printf("state: %u (binary %u%u)\r\n", state, (state >> 1) & 1, state & 1);

  state = (state + 1) % 2;
  delay(1000);
}
