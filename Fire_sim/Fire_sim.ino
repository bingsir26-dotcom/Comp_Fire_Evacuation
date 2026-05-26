// 慧焰導行 – 火焰模擬器（旋鈕控制版）
// 5 個旋鈕 (A1-A5) 控制 5 個 LED (3,6,7,9,11) 的亮度
// 透過 Serial (115200) 輸出數據給 Python 後端

const int POT_PINS[] = {A1, A2, A3, A4, A5};
const int LED_PINS[] = {3, 6, 7, 9, 11};
const int COUNT = 5;

void setup() {
  Serial.begin(115200);
  for (int i = 0; i < COUNT; i++) {
    pinMode(LED_PINS[i], OUTPUT);
  }
}

void loop() {
  for (int i = 0; i < COUNT; i++) {
    int potVal = analogRead(POT_PINS[i]);        // 0-1023
    int brightness = map(potVal, 0, 1023, 0, 255); // 映射到 0-255
    brightness = constrain(brightness, 0, 255);

    analogWrite(LED_PINS[i], brightness);

    // 格式對應 server.py: 燈%d: 旋鈕=%d, 亮度=%d
    Serial.print("燈");
    Serial.print(i + 1);
    Serial.print(": 旋鈕=");
    Serial.print(potVal);
    Serial.print(", 亮度=");
    Serial.println(brightness);
  }
  delay(200); // 200ms 更新一次，對應 server.py WebSocket 推送節奏
}