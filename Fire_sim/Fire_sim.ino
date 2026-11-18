const int POT_PINS[] = {A1, A2, A3, A4, A5};
const int LED_PINS[] = {3, 6, 10, 9, 11};
const int COUNT = 5;

void setup() {
  Serial.begin(115200);

  for (int i = 0; i < COUNT; i++) {
    pinMode(LED_PINS[i], OUTPUT);
  }
}

void loop() {
  Serial.print("DATA,");

  for (int i = 0; i < COUNT; i++) {
    int potVal = analogRead(POT_PINS[i]);
    int brightness = map(potVal, 0, 1023, 0, 255);
    brightness = constrain(brightness, 0, 255);

    analogWrite(LED_PINS[i], brightness);

    Serial.print(potVal);
    Serial.print(",");
    Serial.print(brightness);

    if (i < COUNT - 1) {
      Serial.print(",");
    }
  }

  Serial.println();
  delay(200);
}