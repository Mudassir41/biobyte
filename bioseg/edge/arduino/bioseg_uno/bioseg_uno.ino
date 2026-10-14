/*
  BioSeg chute controller for an Arduino Uno.

  Two physical bins only:
    LEFT  -> yellow (gauze, dressings)
    RIGHT -> red (gloves, plastic)
    HOLD  -> gate shut. The item stays in the chute. This is not a third bin.

  White (sharps) and blue (glass) are holds. Another station reports whether
  a bag was processed. This board does not measure temperature.

  Wiring
    D9  servo signal. Servo red to an external 5 V supply, not the Uno 5 V pin.
        Servo brown and supply ground tied to Uno GND.
    D2  IR obstacle module OUT. Most modules go LOW when an object is present.
    D8  passive buzzer, optional.
    USB serial to the Pi at 115200.

  Pi -> Uno:  LEFT | RIGHT | HOLD | PING
  Uno -> Pi:  OK LEFT | OK RIGHT | OK HOLD | PONG | TRIG

  Tune the three angles to the horn you actually fit. Shut must block both drops.
*/

#include <Servo.h>

const int PIN_SERVO = 9;
const int PIN_IR = 2;
const int PIN_BUZZER = 8;

const int ANGLE_YELLOW = 40;
const int ANGLE_RED = 140;
const int ANGLE_SHUT = 90;

const unsigned long IR_DEBOUNCE_MS = 300;

Servo flap;
bool irArmed = true;
unsigned long lastTrigMs = 0;

void setFlap(int angle) {
  flap.write(angle);
}

void beepHold() {
  tone(PIN_BUZZER, 880, 180);
}

void handleCommand(String line) {
  line.trim();
  if (line == "LEFT") {
    setFlap(ANGLE_YELLOW);
    Serial.println("OK LEFT");
  } else if (line == "RIGHT") {
    setFlap(ANGLE_RED);
    Serial.println("OK RIGHT");
  } else if (line == "HOLD") {
    setFlap(ANGLE_SHUT);
    beepHold();
    Serial.println("OK HOLD");
  } else if (line == "PING") {
    Serial.println("PONG");
  }
}

void setup() {
  Serial.begin(115200);
  pinMode(PIN_IR, INPUT_PULLUP);
  pinMode(PIN_BUZZER, OUTPUT);
  flap.attach(PIN_SERVO);
  setFlap(ANGLE_SHUT);
  Serial.println("OK HOLD");
}

void loop() {
  if (Serial.available()) {
    handleCommand(Serial.readStringUntil('\n'));
  }

  bool blocked = digitalRead(PIN_IR) == LOW;
  unsigned long now = millis();
  if (blocked && irArmed && (now - lastTrigMs) > IR_DEBOUNCE_MS) {
    lastTrigMs = now;
    irArmed = false;
    Serial.println("TRIG");
  }
  if (!blocked) {
    irArmed = true;
  }
}
