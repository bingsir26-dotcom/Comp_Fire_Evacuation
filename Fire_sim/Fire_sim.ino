#include <Wire.h>
#include <Adafruit_MotorShield.h>

Adafruit_MotorShield AFMS = Adafruit_MotorShield();

Adafruit_DCMotor *motor1 = AFMS.getMotor(1);
Adafruit_DCMotor *motor2 = AFMS.getMotor(2);
Adafruit_DCMotor *motor3 = AFMS.getMotor(3);
Adafruit_DCMotor *motor4 = AFMS.getMotor(4);

void setup() {
  Serial.begin(9600);

  if (!AFMS.begin()) {
    Serial.println("ERROR: Motor Shield not found.");

    while (true) {
      delay(100);
    }
  }

  Serial.println("Motor Shield OK. Start in 3 seconds.");

  motor1->run(RELEASE);
  motor2->run(RELEASE);
  motor3->run(RELEASE);
  motor4->run(RELEASE);

  motor1->setSpeed(150);
  motor2->setSpeed(150);
  motor3->setSpeed(150);
  motor4->setSpeed(150);

  delay(3000);
}

void loop() {
  Serial.println("MOTORS ON");

  motor1->run(FORWARD);
  motor2->run(FORWARD);
  motor3->run(FORWARD);
  motor4->run(FORWARD);

  delay(2000);

  Serial.println("MOTORS OFF");

  motor1->run(RELEASE);
  motor2->run(RELEASE);
  motor3->run(RELEASE);
  motor4->run(RELEASE);

  delay(2000);
}