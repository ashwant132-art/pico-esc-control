// Raspberry Pi Pico (Arduino-Pico core) -> BLHeli_S ESC throttle + serial REPL
//
// Talk to it from the PC app, or from any serial terminal (115200 baud, send "\n" line endings).
// Type "help" for the command list.
//
// Wiring: Pico GP17 -> ESC signal, Pico GND -> ESC GND. Do NOT connect the ESC's 5V BEC to the
// Pico. ESC power comes from the battery / bench supply, not from USB.

#include <Servo.h>
#include <EEPROM.h>

const uint8_t ESC_PIN     = 17;
const int     HARD_MIN_US = 800;    // absolute limits for any pulse we will ever output
const int     HARD_MAX_US = 2200;

// factory defaults (used when nothing is saved in flash)
const int      DEF_MIN_US      = 1000;
const int      DEF_START_US    = 1187;
const int      DEF_MAX_US      = 1830;
const int      DEF_SLEW_PCT    = 1;
const uint32_t DEF_TIMEOUT_MS  = 1000;

// runtime-adjustable settings
int      minUs     = DEF_MIN_US;   // 0 % pulse (also the arming pulse)
int      startUs   = DEF_START_US; // 1 % pulse = lowest pulse where the motor actually spins
int      maxUs     = DEF_MAX_US;   // 100 % pulse = where the motor stops speeding up
int      slewPct   = DEF_SLEW_PCT; // max change per 20 ms tick, in % of the start..max range
uint32_t timeoutMs = DEF_TIMEOUT_MS; // failsafe: no command/ping for this long -> cut throttle (0 = off)
bool     debugOn   = false;  // periodic status lines

Servo    esc;
int      targetUs  = minUs;
int      currentUs = minUs;
bool     tripped   = false;  // failsafe has fired and is latched until a new throttle command
uint32_t lastPing = 0, lastTick = 0, lastDebug = 0;
String   line;

// 0 % -> minUs (off); 1..100 % -> startUs..maxUs linearly
int pctToUs(int p) {
  if (p <= 0) return minUs;
  return startUs + (long)(maxUs - startUs) * (p - 1) / 99;
}
int usToPct(int us) {
  if (us < startUs) return 0;
  long span = maxUs - startUs;
  return constrain(1 + ((long)(us - startUs) * 99 + span / 2) / span, 1, 100);   // rounded, not truncated
}

bool isNumber(const String &s) {
  if (!s.length()) return false;
  for (unsigned i = 0; i < s.length(); i++) if (!isDigit(s[i])) return false;
  return true;
}

// ---- persistent settings (emulated EEPROM in flash) ----
struct Settings {
  uint32_t magic;
  int32_t  minUs, startUs, maxUs, slewPct;
  uint32_t timeoutMs;
};
const uint32_t SETTINGS_MAGIC = 0xE5C00001;
const char    *cfgState = "defaults";   // "defaults" | "flash" | "modified" (changed since load/save)

void loadSettings() {
  Settings st;
  EEPROM.get(0, st);
  bool ok = st.magic == SETTINGS_MAGIC
         && st.minUs >= HARD_MIN_US && st.minUs < st.startUs
         && st.startUs <= st.maxUs - 50 && st.maxUs <= HARD_MAX_US
         && st.slewPct >= 1 && st.slewPct <= 100
         && (st.timeoutMs == 0 || (st.timeoutMs >= 200 && st.timeoutMs <= 60000));
  if (!ok) return;                      // nothing saved, or data looks corrupt -> keep defaults
  minUs = st.minUs; startUs = st.startUs; maxUs = st.maxUs;
  slewPct = st.slewPct; timeoutMs = st.timeoutMs;
  targetUs = currentUs = minUs;
  cfgState = "flash";
}

// NB: no function may take/return "Settings" - the Arduino IDE auto-generates prototypes above the struct.
bool writeSettings(bool valid) {          // valid=false writes a blank record (= "nothing saved")
  Settings st = { valid ? SETTINGS_MAGIC : 0u, minUs, startUs, maxUs, slewPct, timeoutMs };
  EEPROM.put(0, st);
  return EEPROM.commit();
}

void setTarget(int us) {
  targetUs = constrain(us, HARD_MIN_US, HARD_MAX_US);
  tripped  = false;
  lastPing = millis();
}

void cutNow() {                       // immediate stop, no ramp
  targetUs = currentUs = minUs;
  esc.writeMicroseconds(minUs);
}

void printStatus(const char *tag) {
  String t = timeoutMs ? String(timeoutMs) + "ms" : String("off");
  Serial.printf("%s pulse=%dus (%d%%) target=%dus map: 0%%=%dus 1%%=%dus 100%%=%dus slew=%d%%/20ms timeout=%s failsafe=%s cfg=%s\n",
                tag, currentUs, usToPct(currentUs), targetUs, minUs, startUs, maxUs,
                slewPct, t.c_str(), tripped ? "TRIPPED" : "ok", cfgState);
}

void printHelp() {
  Serial.println("Commands:");
  Serial.println("  <0-100>        set throttle % (silent, used by the PC app)");
  Serial.println("  s <0-100>      set throttle % (with confirmation)");
  Serial.println("  us <800-2200>  set raw pulse width in microseconds (debug)");
  Serial.println("  stop           cut throttle immediately");
  Serial.println("  status         print current state");
  Serial.println("  min <us>       set 0% (off) pulse (default 1000)");
  Serial.println("  start <us>     set 1% pulse = where the motor starts (default 1187)");
  Serial.println("  max <us>       set 100% pulse = where speed stops rising (default 1830)");
  Serial.println("  slew <1-100>   ramp limit, % per 20 ms (default 1)");
  Serial.println("  timeout <ms>   failsafe time, 200-60000, 0 = disable (default 1000)");
  Serial.println("  debug on|off   stream status twice a second");
  Serial.println("  save           store min/start/max/slew/timeout in flash (kept after power-off)");
  Serial.println("  defaults       restore factory values and erase what was saved");
  Serial.println("  arm            hold min pulse for 3 s (use after ESC power-up)");
  Serial.println("  ping           refresh failsafe timer (no reply)");
  Serial.println("  reboot         restart the Pico");
  Serial.println("  bootsel        restart into USB bootloader (for flashing)");
  Serial.println("Note: with failsafe on, a serial terminal must send 'ping' at least every second");
  Serial.println("or throttle is cut. Use 'timeout 0' for hands-on typing tests.");
}

void handleLine(String l) {
  l.trim();
  if (!l.length()) return;

  String cmd = l, arg = "";
  int sp = l.indexOf(' ');
  if (sp > 0) { cmd = l.substring(0, sp); arg = l.substring(sp + 1); arg.trim(); }
  cmd.toLowerCase();
  arg.toLowerCase();

  if (cmd == "ping") { lastPing = millis(); return; }

  if (isNumber(cmd) && arg == "") {           // bare number = silent throttle %
    setTarget(pctToUs(constrain(cmd.toInt(), 0, 100)));
    return;
  }

  if (cmd == "help" || cmd == "?") {
    printHelp();
  } else if (cmd == "s") {
    if (!isNumber(arg)) { Serial.println("ERR usage: s <0-100>"); return; }
    int p = constrain(arg.toInt(), 0, 100);
    setTarget(pctToUs(p));
    Serial.printf("OK throttle %d%% -> %dus\n", p, targetUs);
  } else if (cmd == "us") {
    int v = arg.toInt();
    if (!isNumber(arg) || v < HARD_MIN_US || v > HARD_MAX_US) {
      Serial.printf("ERR usage: us <%d-%d>\n", HARD_MIN_US, HARD_MAX_US); return;
    }
    setTarget(v);
    Serial.printf("OK raw pulse target %dus\n", targetUs);
  } else if (cmd == "stop") {
    cutNow();
    tripped  = false;
    lastPing = millis();
    Serial.println("OK stopped");
  } else if (cmd == "status") {
    printStatus("STATUS");
  } else if (cmd == "min") {
    int v = arg.toInt();
    if (!isNumber(arg) || v < HARD_MIN_US || v >= startUs) {
      Serial.printf("ERR min must be %d-%d\n", HARD_MIN_US, startUs - 1); return;
    }
    minUs = v; cfgState = "modified"; cutNow();
    Serial.printf("OK min=%dus (throttle cut)\n", minUs);
  } else if (cmd == "start") {
    int v = arg.toInt();
    if (!isNumber(arg) || v <= minUs || v > maxUs - 50) {
      Serial.printf("ERR start must be %d-%d\n", minUs + 1, maxUs - 50); return;
    }
    startUs = v; cfgState = "modified"; cutNow();
    Serial.printf("OK start=%dus (1%%), throttle cut\n", startUs);
  } else if (cmd == "max") {
    int v = arg.toInt();
    if (!isNumber(arg) || v < startUs + 50 || v > HARD_MAX_US) {
      Serial.printf("ERR max must be %d-%d\n", startUs + 50, HARD_MAX_US); return;
    }
    maxUs = v; cfgState = "modified"; cutNow();
    Serial.printf("OK max=%dus (throttle cut)\n", maxUs);
  } else if (cmd == "slew") {
    int v = arg.toInt();
    if (!isNumber(arg) || v < 1 || v > 100) { Serial.println("ERR usage: slew <1-100>"); return; }
    slewPct = v;
    cfgState = "modified";
    Serial.printf("OK slew=%d%%/20ms\n", slewPct);
  } else if (cmd == "timeout") {
    int v = arg.toInt();
    if (!isNumber(arg) || (v != 0 && (v < 200 || v > 60000))) {
      Serial.println("ERR usage: timeout <0 or 200-60000>"); return;
    }
    timeoutMs = v;
    cfgState = "modified";
    lastPing  = millis();
    Serial.printf("OK timeout=%s\n", v ? (String(v) + "ms").c_str() : "off (failsafe disabled)");
  } else if (cmd == "debug") {
    if (arg == "on")       debugOn = true;
    else if (arg == "off") debugOn = false;
    else { Serial.println("ERR usage: debug on|off"); return; }
    Serial.printf("OK debug %s\n", debugOn ? "on" : "off");
  } else if (cmd == "save") {
    cutNow();                                   // flash write briefly stalls the CPU: stop the motor first
    if (writeSettings(true)) {
      cfgState = "flash";
      Serial.printf("OK saved to flash: 0%%=%dus 1%%=%dus 100%%=%dus slew=%d timeout=%lu (throttle cut)\n",
                    minUs, startUs, maxUs, slewPct, (unsigned long)timeoutMs);
    } else {
      Serial.println("ERR flash write failed, nothing saved");
    }
  } else if (cmd == "defaults") {
    cutNow();
    minUs = DEF_MIN_US; startUs = DEF_START_US; maxUs = DEF_MAX_US;
    slewPct = DEF_SLEW_PCT; timeoutMs = DEF_TIMEOUT_MS;
    targetUs = currentUs = minUs;
    writeSettings(false);                       // magic = 0 -> treated as "nothing saved"
    cfgState = "defaults";
    Serial.println("OK factory defaults restored, saved settings erased");
  } else if (cmd == "arm") {
    cutNow();
    Serial.println("OK arming: holding min pulse for 3 s...");
    delay(3000);
    lastPing = millis();
    Serial.println("OK armed");
  } else if (cmd == "reboot") {
    cutNow();
    Serial.println("OK rebooting");
    Serial.flush();
    delay(100);
    rp2040.reboot();
  } else if (cmd == "bootsel") {
    cutNow();
    Serial.println("OK rebooting into USB bootloader");
    Serial.flush();
    delay(100);
    rp2040.rebootToBootloader();
  } else {
    Serial.printf("ERR unknown command '%s' (try: help)\n", cmd.c_str());
  }
}

void setup() {
  Serial.begin(115200);
  EEPROM.begin(256);
  esc.attach(ESC_PIN, 500, 2500);         // wide library limits; we enforce our own
  loadSettings();                         // saved values (if any) replace the defaults
  esc.writeMicroseconds(minUs);           // arm: hold minimum throttle
  delay(3000);                            // let the ESC finish its startup beeps
  lastPing = millis();
}

void loop() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n' || c == '\r') {
      if (line.length()) { handleLine(line); line = ""; }
    } else if (line.length() < 40) {
      line += c;
    }
  }

  if (timeoutMs && !tripped && millis() - lastPing > timeoutMs &&
      (targetUs != minUs || currentUs != minUs)) {
    targetUs = minUs;
    tripped  = true;
    Serial.printf("WARN failsafe: nothing received for %lums, throttle cut\n", (unsigned long)timeoutMs);
  }

  if (millis() - lastTick >= 20) {
    lastTick = millis();
    int step = max(1, (maxUs - startUs) * slewPct / 100);
    if (targetUs >= startUs && currentUs < startUs)        currentUs = startUs;
    else if (targetUs == minUs && currentUs <= startUs)    currentUs = minUs;
    else if (currentUs < targetUs)                         currentUs = min(currentUs + step, targetUs);
    else if (currentUs > targetUs)                         currentUs = max(currentUs - step, targetUs);
    esc.writeMicroseconds(currentUs);
  }

  if (debugOn && millis() - lastDebug >= 500) {
    lastDebug = millis();
    printStatus("DBG");
  }
}
