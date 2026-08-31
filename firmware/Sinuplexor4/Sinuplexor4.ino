/*
 * Sinuplexor 4 — ECG vest electrode detection
 *
 * Detects which of the 10 vest electrodes are correctly seated by reading a
 * per-electrode resistor divider through a 16-channel analog mux (CD74HC4067).
 * Each electrode carries a distinct divider value, so this detects both a
 * MISSING electrode and an electrode plugged into the WRONG socket.
 *
 * Changes from Sinuplexor3 — all three exist to make the web app reliable:
 *
 *   1. State is re-announced once per second in addition to being sent
 *      immediately on change. v3 printed ONLY inside setECGState(), i.e. only
 *      on a transition. Since the board boots and settles the moment USB power
 *      arrives, a browser connecting afterwards would hear nothing at all and
 *      hang at "disconnected" until the student physically unplugged an
 *      electrode. The 1 Hz heartbeat also lets the app detect an unplugged or
 *      hung board (no line for ~3 s). Transitions are still sent instantly —
 *      this is 1 line/s at idle, not a stream.
 *   2. Framed, self-identifying protocol instead of a bare "1"/"0", so a stray
 *      byte or an unrelated device on the port cannot be misread as a state.
 *   3. All 10 electrodes are measured and reported as a bitmask. C1 stays
 *      excluded from the pass/fail gate but is still reported, so a repaired
 *      C1 shows up in the data without reflashing, and the app can name which
 *      electrode fell off instead of just saying "something is wrong".
 *
 * Serial protocol — 9600 8N1, '\n' terminated. One line on every state
 * change, plus one line per second as a heartbeat:
 *
 *   V,<ok>,<mask>,<count>
 *     ok     1 = all required electrodes seated (debounced), 0 = not
 *     mask   decimal 10-bit bitmap; bit i set = electrode i reads correct
 *            bit 0=N  1=C1  2=C2  3=C3  4=C4  5=C5  6=C6  7=R  8=L  9=F
 *     count  number of REQUIRED electrodes currently correct (0..9)
 *
 *   Example:  V,1,1023,9    every electrode seated
 *             V,0,1015,8    bit 3 clear -> C3 is off
 *
 *   Once on boot:  # SINUPLEXOR 4 REQ=1021 IGN=2
 *   Lines beginning with '#' are informational. Consumers MUST ignore any
 *   line that does not begin with "V,".
 */

const int muxSIG = A7;

const int S0 = 2;
const int S1 = 3;
const int S2 = 4;
const int S3 = 5;

const int ledRojo  = 6;
const int ledVerde = 7;

const int NUM_ELECTRODOS = 10;
const int TOLERANCE      = 30;

// Asymmetric hysteresis: slow to trust a good vest, quick to drop a bad one.
const int SCANS_TO_ENABLE  = 5;
const int SCANS_TO_DISABLE = 3;

// Electrodes excluded from the pass/fail gate but still measured and reported.
// bit 1 = C1 (divider not functional on the current hardware revision).
const uint16_t IGNORED_MASK  = (1 << 1);
const uint16_t ALL_MASK      = (1 << NUM_ELECTRODOS) - 1;  // 0x3FF = 1023
const uint16_t REQUIRED_MASK = ALL_MASK & ~IGNORED_MASK;   // 0x3FD = 1021

const int expectedADC[NUM_ELECTRODOS] = {
  1020,  // 0  N
   920,  // 1  C1  (ignored — see IGNORED_MASK)
   818,  // 2  C2
   716,  // 3  C3
   614,  // 4  C4
   512,  // 5  C5
   410,  // 6  C6
   308,  // 7  R
   206,  // 8  L
   104   // 9  F
};

// Re-announce state this often even when nothing changes, so an app that
// connects mid-session learns the current state without waiting for a change.
const unsigned long HEARTBEAT_MS = 1000;

bool ecgEnabled = false;

int goodScans = 0;
int badScans  = 0;

unsigned long lastReport = 0;


void setup()
{
  pinMode(S0, OUTPUT);
  pinMode(S1, OUTPUT);
  pinMode(S2, OUTPUT);
  pinMode(S3, OUTPUT);

  pinMode(ledRojo, OUTPUT);
  pinMode(ledVerde, OUTPUT);

  digitalWrite(ledRojo, HIGH);
  digitalWrite(ledVerde, LOW);

  Serial.begin(9600);

  Serial.print(F("# SINUPLEXOR 4 REQ="));
  Serial.print(REQUIRED_MASK);
  Serial.print(F(" IGN="));
  Serial.println(IGNORED_MASK);
}


void setMuxChannel(byte channel)
{
  digitalWrite(S0, bitRead(channel, 0));
  digitalWrite(S1, bitRead(channel, 1));
  digitalWrite(S2, bitRead(channel, 2));
  digitalWrite(S3, bitRead(channel, 3));
}


int readMux()
{
  delayMicroseconds(50);

  // First conversion after switching the mux is discarded: the ADC
  // sample-and-hold still carries charge from the previous channel.
  analogRead(muxSIG);

  long sum = 0;

  for (int i = 0; i < 4; i++)
  {
    sum += analogRead(muxSIG);
  }

  return sum / 4;
}


bool electrodeIsCorrect(int electrode)
{
  setMuxChannel(electrode);

  int value = readMux();
  int error = abs(value - expectedADC[electrode]);

  return error <= TOLERANCE;
}


void setECGState(bool enabled)
{
  ecgEnabled = enabled;

  digitalWrite(ledRojo,  enabled ? LOW  : HIGH);
  digitalWrite(ledVerde, enabled ? HIGH : LOW);
}


void reportState(uint16_t mask)
{
  int required = 0;

  for (int i = 0; i < NUM_ELECTRODOS; i++)
  {
    if ((REQUIRED_MASK & (1 << i)) && (mask & (1 << i)))
    {
      required++;
    }
  }

  Serial.print(F("V,"));
  Serial.print(ecgEnabled ? 1 : 0);
  Serial.print(',');
  Serial.print(mask);
  Serial.print(',');
  Serial.println(required);
}


void loop()
{
  uint16_t mask = 0;

  // Measure every electrode, including ignored ones, so the mask is complete.
  for (int i = 0; i < NUM_ELECTRODOS; i++)
  {
    if (electrodeIsCorrect(i))
    {
      mask |= (1 << i);
    }
  }

  bool allCorrect = ((mask & REQUIRED_MASK) == REQUIRED_MASK);
  bool changed = false;

  if (allCorrect)
  {
    badScans = 0;
    if (goodScans < SCANS_TO_ENABLE) goodScans++;   // saturate, never overflow

    if (!ecgEnabled && goodScans >= SCANS_TO_ENABLE)
    {
      setECGState(true);
      changed = true;
    }
  }
  else
  {
    goodScans = 0;
    if (badScans < SCANS_TO_DISABLE) badScans++;

    if (ecgEnabled && badScans >= SCANS_TO_DISABLE)
    {
      setECGState(false);
      changed = true;
    }
  }

  // Instant on transition, otherwise once per second.
  if (changed || (millis() - lastReport >= HEARTBEAT_MS))
  {
    reportState(mask);
    lastReport = millis();
  }

  delay(50);
}
