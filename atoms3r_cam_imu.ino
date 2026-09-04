/*
 * AtomS3R-CAM  ->  endoscope tip: camera + orientation over ONE USB cable (v4)
 * ----------------------------------------------------------------------------
 * Streams JPEG frames and a fused orientation quaternion to the Raspberry Pi
 * over the USB CDC serial link.
 *
 * BOARD SETTINGS (the wrong board is what broke earlier builds)
 *   Board             M5Stack -> M5AtomS3R      <- the R matters; plain
 *                                                  M5AtomS3 has no PSRAM and
 *                                                  the frame buffer alloc fails
 *   USB CDC On Boot   Enabled
 *   USB Mode          Hardware CDC and JTAG
 *
 * PACKET FORMAT (unchanged since v1 -- old and new Pi apps interoperate)
 *   0xA5 0x5A | type(1) | length(4, LE) | payload | checksum(1, XOR)
 *   type 1 = IMU JSON text, type 2 = JPEG frame
 *
 * WHY QUATERNIONS AND NOT pitch/roll/yaw
 *   Euler angles gimbal-lock when the probe points straight up or down --
 *   exactly what an endoscope does when it looks into a hole. The indicator
 *   would flip. A quaternion has no such singularity.
 *
 * DRIFT HANDLING (the thing that decides whether this product works)
 *   1. Boot calibration measures the gyro zero-offset, retrying up to three
 *      times if the probe was moving. A failed calibration keeps bias ZERO:
 *      an average taken while moving is far worse than no correction at all
 *      (v2 installed it anyway).
 *   2. A Mahony complementary filter uses gravity to hold pitch and roll and
 *      to keep re-estimating the X/Y gyro bias while running.
 *   3. Gravity says nothing about heading, so the Z (yaw) bias is re-learned
 *      whenever the probe is detected motionless. An endoscope pauses often,
 *      so in practice this keeps heading usable between manual re-zeros. The
 *      learned bias is clamped near the calibrated value so a slow genuine
 *      pan can never be permanently mistaken for offset.
 *
 * WHAT v3 CHANGES OVER v2
 *   v2 compressed and sent each frame inside loop(): 50-80 ms during which no
 *   gyro sample was taken, i.e. a hole punched in the attitude integration
 *   twelve times a second, exactly when the probe might be moving. v3 runs
 *   the camera pipeline in its own FreeRTOS task on core 0 (loop() lives on
 *   core 1), so the IMU integrates at a steady 100 Hz regardless of what the
 *   encoder is doing. Packet writes are serialised by a mutex so the two
 *   streams can never interleave inside one packet. Set USE_CAMERA_TASK to 0
 *   to get v2's single-loop behaviour back if the task split ever needs to be
 *   ruled out while debugging.
 *
 * VIDEO PATH IN v4
 *   Production mode now follows M5Stack's official AtomS3R-CAM examples:
 *       esp_camera_fb_get() -> frame2jpg(fb, 60, ...) -> transport
 *   There is no hand-written RGB565 unpack, byte swap, YUV reinterpretation,
 *   white balance or colour matrix in that path. Those experiments were the
 *   source of the false-colour regressions. Legacy modes remain available for
 *   diagnostics, but mode 0 is the only production/default path.
 *
 * Verified pins from m5stack/M5AtomS3 examples/Basics/camera/camera_pins.h.
 * Sensor reports PID 0x009B (GC0308).
 */

#include <M5Unified.h>
#include "esp_camera.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"

#define USE_CAMERA_TASK 1     // 0 = v2 fallback: camera inline in loop()

// ---------------------------------------------------------------- pin map
#define PWDN_GPIO_NUM  -1     // power handled manually via POWER_GPIO_NUM
#define RESET_GPIO_NUM -1
#define XCLK_GPIO_NUM  21
#define SIOD_GPIO_NUM  12
#define SIOC_GPIO_NUM   9
#define Y9_GPIO_NUM    13
#define Y8_GPIO_NUM    11
#define Y7_GPIO_NUM    17
#define Y6_GPIO_NUM     4
#define Y5_GPIO_NUM    48
#define Y4_GPIO_NUM    46
#define Y3_GPIO_NUM    42
#define Y2_GPIO_NUM     3
#define VSYNC_GPIO_NUM 10
#define HREF_GPIO_NUM  14
#define PCLK_GPIO_NUM  40
#define POWER_GPIO_NUM 18     // drive LOW to power the sensor

// ---------------------------------------------------------------- tuning

/*
 * VIDEO MODES. Mode 0 is the M5Stack official conversion path and is the
 * production default. The other modes are retained only so old diagnostic
 * clients remain wire-compatible:
 *
 *   '0'  official   frame2jpg(camera_fb_t*) exactly as M5Stack does
 *   '1'  legacy     manual RGB565 little-endian unpack (diagnostic only)
 *   '2'  yuv        interpret the buffer as YUV422 instead of RGB565 --
 *                   covers the sensor ignoring the RGB565 request and
 *                   streaming its native YCbCr, which no byte swap can fix
 *   'c'  cycle 0 -> 1 -> 2 -> 0
 *
 * Every change is acknowledged with {"status":"colour_mode","mode":n} and
 * the current mode rides in the ready line as "cm".
 */
static const uint8_t COLOUR_MODE_BOOT = 0;     // 0 official / 1 legacy / 2 yuv
volatile uint8_t colourMode = COLOUR_MODE_BOOT;

static const uint32_t IMU_PERIOD_MS   = 10;    // integrate at 100 Hz...
static const uint8_t  IMU_SEND_DIV    = 2;     // ...transmit every 2nd = 50 Hz
static const uint32_t FRAME_PERIOD_MS = 80;    // ~12 fps
static const int      JPEG_QUALITY    = 60;   // the value M5's UVC service uses
static const uint16_t CALIB_COUNT     = 300;
static const uint8_t  CALIB_TRIES     = 3;

// Mahony gains. KP pulls the estimate toward gravity; KI slowly absorbs the
// X/Y gyro bias. Both act only through the accelerometer, so neither touches
// heading.
static const float KP = 2.0f;
static const float KI = 0.05f;

// Motionless detection, used to re-learn the Z bias that gravity cannot see.
// 1.5 deg/s catches a probe resting inside a bore but is above hand tremor,
// so a deliberate slow pan is less likely to be eaten as "offset"; the clamp
// below bounds the damage even if it is.
static const float STILL_GYRO_DPS = 1.5f;    // residual rate below this
static const float STILL_ACC_TOL  = 0.06f;   // |accel| within this of 1 g
static const uint32_t STILL_MS    = 500;     // must hold this long first
static const float BIAS_LEARN     = 0.001f;  // per-sample @100 Hz (tau ~10 s)
static const float BIAS_CLAMP_DPS = 3.0f;    // max learned drift from calib

static const uint8_t TYPE_IMU   = 1;
static const uint8_t TYPE_FRAME = 2;
static const uint8_t TYPE_RAW   = 3;   // one uncompressed RGB565 frame,
                                       // for the Pi's DIAG interpretation grid

volatile bool rawOnce = false;         // send the next frame raw, untouched
/*
 * RAW STREAM. Every colour argument so far has been about how these bytes get
 * turned into a picture inside the firmware. This mode declines to do it:
 * the sensor's RGB565 goes out untouched and the Pi decodes it with OpenCV's
 * standard cvtColor. Nothing here can misread a byte because nothing here
 * reads one. Halved to 160x120 because raw is 4x the size of the JPEG and the
 * CDC link has to carry the IMU as well.
 *
 * If the picture is still wrong under this mode, the fault is not pixel
 * interpretation at all -- it is the parallel bus or the sensor itself, which
 * is a different hunt entirely. That makes this mode the deciding test.
 */
volatile bool rawStream = false;

/*
 * TEST PATTERN. Every colour theory so far has been inferred from photographs
 * of an unknown scene, which is why five rounds produced five wrong answers:
 * a picture cannot tell you what it was supposed to look like.
 *
 * This puts a KNOWN image into the pipeline. Eight colour bars and a grey
 * ramp are generated here in RGB565 and travel the identical path a camera
 * frame takes -- same unpack, same encoder, same packets, same decoder.
 *
 *   bars come out clean  -> the pipeline is correct end to end, and the fault
 *                           is in the pixels the SENSOR produces: bus timing,
 *                           XCLK, or the sensor's own configuration.
 *   bars come out wrong  -> the pipeline is at fault, and HOW they are wrong
 *                           names the fault exactly, because the input is
 *                           known: shifted bars mean a stride error, swapped
 *                           colours mean a channel order error, banding means
 *                           a bit-field error.
 *
 * Either answer ends the guessing. Toggled with 't'.
 */
volatile bool testPattern = false;



static void makeTestPattern(uint16_t *dst, int w, int h) {
  // RGB565: white, yellow, cyan, green, magenta, red, blue, black
  static const uint16_t bars[8] = {
    0xFFFF, 0xFFE0, 0x07FF, 0x07E0, 0xF81F, 0xF800, 0x001F, 0x0000
  };
  const int split = (h * 3) / 4;
  for (int y = 0; y < h; y++) {
    uint16_t *row = dst + (size_t)y * w;
    if (y < split) {
      for (int x = 0; x < w; x++) row[x] = bars[(x * 8) / w];
    } else {
      // Grey ramp: a bit-field error shows here as coloured banding rather
      // than a clean fade.
      for (int x = 0; x < w; x++) {
        int v = (x * 255) / (w - 1);
        row[x] = (uint16_t)(((v >> 3) << 11) | ((v >> 2) << 5) | (v >> 3));
      }
    }
  }
}

// Orientation state: unit quaternion, body -> world, world Z is up.
// Touched only by loop() / the IMU path on core 1.
float q0 = 1, q1 = 0, q2 = 0, q3 = 0;
float biasX = 0, biasY = 0, biasZ = 0;     // deg/s, in use
float calX = 0,  calY = 0,  calZ = 0;      // deg/s, as calibrated (clamp anchor)
float intX = 0, intY = 0, intZ = 0;        // Mahony integral term, rad/s
uint32_t lastImu = 0, lastMicros = 0;
uint32_t stillSince = 0;
uint8_t  imuTick = 0;
bool isStill = false;
bool cameraReady = false;
bool calibOk = false;

SemaphoreHandle_t txMux = NULL;            // one packet on the wire at a time

static camera_config_t camera_config = {
    .pin_pwdn     = PWDN_GPIO_NUM,
    .pin_reset    = RESET_GPIO_NUM,
    .pin_xclk     = XCLK_GPIO_NUM,
    .pin_sccb_sda = SIOD_GPIO_NUM,
    .pin_sccb_scl = SIOC_GPIO_NUM,
    .pin_d7       = Y9_GPIO_NUM,
    .pin_d6       = Y8_GPIO_NUM,
    .pin_d5       = Y7_GPIO_NUM,
    .pin_d4       = Y6_GPIO_NUM,
    .pin_d3       = Y5_GPIO_NUM,
    .pin_d2       = Y4_GPIO_NUM,
    .pin_d1       = Y3_GPIO_NUM,
    .pin_d0       = Y2_GPIO_NUM,
    .pin_vsync    = VSYNC_GPIO_NUM,
    .pin_href     = HREF_GPIO_NUM,
    .pin_pclk     = PCLK_GPIO_NUM,
    .xclk_freq_hz = 20000000,
    .ledc_timer   = LEDC_TIMER_0,
    .ledc_channel = LEDC_CHANNEL_0,
    // The GC0308 has no hardware JPEG encoder, so frames arrive as RGB565 and
    // frame2jpg() compresses them in software.
    .pixel_format = PIXFORMAT_RGB565,
    .frame_size   = FRAMESIZE_QVGA,
    .jpeg_quality = 12,
    .fb_count     = 2,
    .fb_location  = CAMERA_FB_IN_PSRAM,
    .grab_mode    = CAMERA_GRAB_WHEN_EMPTY,   // as the official firmware
    .sccb_i2c_port = 0,          // port 1 belongs to M5Unified / the IMU
};

// ---------------------------------------------------------------- protocol

void sendPacket(uint8_t type, const uint8_t *payload, uint32_t len) {
  // Two tasks write packets; a packet must hit the wire whole or the Pi has
  // to burn bytes resynchronising. Held across header+payload+checksum.
  if (txMux) xSemaphoreTake(txMux, portMAX_DELAY);

  uint8_t header[7];
  header[0] = 0xA5;
  header[1] = 0x5A;
  header[2] = type;
  header[3] = (uint8_t)(len & 0xFF);
  header[4] = (uint8_t)((len >> 8) & 0xFF);
  header[5] = (uint8_t)((len >> 16) & 0xFF);
  header[6] = (uint8_t)((len >> 24) & 0xFF);
  Serial.write(header, 7);

  uint32_t sent = 0;                 // chunked: one huge write overruns CDC
  while (sent < len) {
    uint32_t chunk = len - sent;
    if (chunk > 512) chunk = 512;
    Serial.write(payload + sent, chunk);
    sent += chunk;
  }

  uint8_t checksum = 0;
  for (uint32_t i = 0; i < len; i++) checksum ^= payload[i];
  Serial.write(&checksum, 1);

  if (txMux) xSemaphoreGive(txMux);
}

void sendText(uint8_t type, const char *text) {
  sendPacket(type, (const uint8_t *)text, strlen(text));
}

// ---------------------------------------------------------------- camera

bool startCamera() {
  pinMode(POWER_GPIO_NUM, OUTPUT);
  digitalWrite(POWER_GPIO_NUM, LOW);
  delay(500);                    // shorter and the sensor is not up yet
  if (esp_camera_init(&camera_config) != ESP_OK) return false;

  // Match m5stack/AtomS3R-CAM-UserDemo camera_init.c. Do not add colour
  // register writes here: the driver's tested init table is the reference.
  sensor_t *s = esp_camera_sensor_get();
  if (s) {
    if (s->set_vflip) s->set_vflip(s, 1);
    if (s->id.PID == GC0308_PID && s->set_hmirror) s->set_hmirror(s, 0);
  }
  return true;
}

/*
 * See COLOUR MODES at the top of the file. Mode 1 fixes the GC0308's
 * reversed RGB565 byte order; mode 2 hands the untouched buffer to the JPEG
 * encoder as YUV422 by reinterpreting a shallow copy of the frame struct.
 */

/*
 * RGB565 -> RGB888, written out in full rather than delegated.
 *
 * frame2jpg()'s own RGB565 handling is where the colour argument kept going
 * in circles, because its byte-order assumption is not visible from here.
 * Doing the unpack ourselves makes the one thing that was ever in doubt --
 * which byte holds the top bits -- a single readable line, and hands the JPEG
 * encoder RGB888, whose layout is not in dispute.
 *
 * The 5- and 6-bit fields are scaled to 8 bits properly (x*255/31 and
 * x*255/63, done in integers) instead of the usual left-shift, which tops out
 * at 248/252 and leaves whites slightly grey.
 */
static uint8_t *rgbBuf = NULL;
static size_t   rgbCap = 0;

static bool ensureRgbBuf(size_t need) {
  if (rgbBuf && rgbCap >= need) return true;
  if (rgbBuf) { free(rgbBuf); rgbBuf = NULL; rgbCap = 0; }
  rgbBuf = (uint8_t *)heap_caps_malloc(need, MALLOC_CAP_SPIRAM);
  if (!rgbBuf) rgbBuf = (uint8_t *)malloc(need);
  if (rgbBuf) rgbCap = need;
  return rgbBuf != NULL;
}

static void unpack565(const uint8_t *src, uint8_t *dst, size_t px, bool le) {
  for (size_t i = 0; i < px; i++) {
    const uint8_t a = src[2 * i], b = src[2 * i + 1];
    const uint16_t v = le ? (uint16_t)(a | (b << 8))
                          : (uint16_t)((a << 8) | b);
    const uint8_t r5 = (v >> 11) & 0x1F;
    const uint8_t g6 = (v >> 5) & 0x3F;
    const uint8_t b5 = v & 0x1F;
    dst[3 * i + 0] = (uint8_t)((r5 * 527 + 23) >> 6);   // 5 bit -> 8 bit
    dst[3 * i + 1] = (uint8_t)((g6 * 259 + 33) >> 6);   // 6 bit -> 8 bit
    dst[3 * i + 2] = (uint8_t)((b5 * 527 + 23) >> 6);
  }
}

void sendFrame() {
  camera_fb_t *fb = esp_camera_fb_get();
  if (!fb) return;

  if (testPattern && fb->format == PIXFORMAT_RGB565) {
    // Overwrite the captured pixels in place. Everything downstream -- the
    // downsample, the unpack, the encoder, the framing -- then runs on known
    // input without a single special case, which is the point.
    makeTestPattern((uint16_t *)fb->buf, fb->width, fb->height);
  }

  if (rawOnce) {
    // Ground truth for colour debugging: the sensor's bytes exactly as they
    // arrived, full size, no conversion, no compression.
    rawOnce = false;
    sendPacket(TYPE_RAW, fb->buf, (uint32_t)fb->len);
    esp_camera_fb_return(fb);
    return;
  }

  if (rawStream && fb->format == PIXFORMAT_RGB565) {
    // Nearest-neighbour 2:1, taking whole pixels so the byte pairing that
    // defines RGB565 is never broken.
    const int w = fb->width, h = fb->height;
    const int hw = w / 2, hh = h / 2;
    if (ensureRgbBuf((size_t)hw * hh * 2)) {
      const uint16_t *src = (const uint16_t *)fb->buf;
      uint16_t *dst = (uint16_t *)rgbBuf;
      for (int y = 0; y < hh; y++) {
        const uint16_t *row = src + (size_t)(y * 2) * w;
        for (int x = 0; x < hw; x++) dst[y * hw + x] = row[x * 2];
      }
      sendPacket(TYPE_RAW, rgbBuf, (uint32_t)((size_t)hw * hh * 2));
      esp_camera_fb_return(fb);
      return;
    }
  }

  uint8_t *jpg = NULL;
  size_t jpgLen = 0;
  bool ok = false;
  const uint8_t mode = colourMode;

  if (mode == 0) {
    // This is deliberately boring: it is the exact conversion used by both
    // official M5Stack examples (Arduino HTTP stream and ESP-IDF UVC demo).
    // The custom packet wrapper transports the finished JPEG byte-for-byte
    // and therefore cannot change its colours.
    ok = frame2jpg(fb, JPEG_QUALITY, &jpg, &jpgLen);
  } else if (fb->format == PIXFORMAT_RGB565 && mode == 1) {
    const size_t px = (size_t)fb->width * fb->height;
    if (ensureRgbBuf(px * 3)) {
      unpack565(fb->buf, rgbBuf, px, true);
      ok = fmt2jpg(rgbBuf, px * 3, fb->width, fb->height,
                   PIXFORMAT_RGB888, JPEG_QUALITY, &jpg, &jpgLen);
    }
  } else if (mode == 2 || mode == 3) {
    if (mode == 3) {                        // chroma-first order -> swap pairs
      uint8_t *p = fb->buf;
      size_t n = fb->len & ~((size_t)1);
      for (size_t i = 0; i < n; i += 2) { uint8_t t = p[i]; p[i] = p[i + 1]; p[i + 1] = t; }
    }
    camera_fb_t alt = *fb;                  // same pixels, read as YUV422
    alt.format = PIXFORMAT_YUV422;
    ok = frame2jpg(&alt, JPEG_QUALITY, &jpg, &jpgLen);
  }

  if (!ok) {                                // safe fallback = official path
    ok = frame2jpg(fb, JPEG_QUALITY, &jpg, &jpgLen);
  }
  esp_camera_fb_return(fb);

  if (ok && jpg && jpgLen > 0) sendPacket(TYPE_FRAME, jpg, (uint32_t)jpgLen);
  if (jpg) free(jpg);
}

#if USE_CAMERA_TASK
/*
 * The whole capture -> official JPEG -> USB pipeline, on core 0.
 * Arduino's loop() runs on core 1, so the IMU never waits for the encoder.
 * Touches nothing but the camera driver and sendPacket(); in particular it
 * never goes near M5Unified or I2C port 1.
 */
void camTask(void *arg) {
  TickType_t wake = xTaskGetTickCount();
  for (;;) {
    vTaskDelayUntil(&wake, pdMS_TO_TICKS(FRAME_PERIOD_MS));
    sendFrame();
  }
}
#endif

// ---------------------------------------------------------------- IMU

bool readImu(float *ax, float *ay, float *az,
             float *gx, float *gy, float *gz) {
  M5.Imu.update();
  if (!M5.Imu.getAccel(ax, ay, az)) return false;
  if (!M5.Imu.getGyro(gx, gy, gz))  return false;
  return true;
}

/*
 * Average a few hundred stationary samples to find the gyro zero-offset.
 * Nothing is committed here: the caller decides what to do with the result,
 * because an average taken while the probe was moving must be thrown away,
 * not installed (v2's mistake).
 */
bool measureGyroBias(float *bx, float *by, float *bz, float *outSpread,
                     bool *sensorAlive) {
  double sx = 0, sy = 0, sz = 0;
  float minv[3] = { 1e9f, 1e9f, 1e9f };
  float maxv[3] = { -1e9f, -1e9f, -1e9f };
  int taken = 0;

  // The BMI270 can spit a few wild samples right after (re)configuration;
  // averaging those in looks exactly like "the probe moved". Let it settle,
  // then burn the first reads before accumulating (spread 153 deg/s was
  // observed on hardware from this).
  delay(250);
  for (uint8_t w = 0; w < 25; w++) {
    float d0, d1, d2, d3, d4, d5;
    readImu(&d0, &d1, &d2, &d3, &d4, &d5);
    delay(4);
  }

  for (uint16_t i = 0; i < CALIB_COUNT; i++) {
    float ax, ay, az, gx, gy, gz;
    if (readImu(&ax, &ay, &az, &gx, &gy, &gz)) {
      sx += gx; sy += gy; sz += gz;
      float v[3] = { gx, gy, gz };
      for (int k = 0; k < 3; k++) {
        if (v[k] < minv[k]) minv[k] = v[k];
        if (v[k] > maxv[k]) maxv[k] = v[k];
      }
      taken++;
    }
    delay(4);
  }

  *sensorAlive = taken >= CALIB_COUNT / 2;
  if (!*sensorAlive) {
    *outSpread = -1.0f;
    return false;                       // sensor never answered
  }

  float spread = 0;
  for (int k = 0; k < 3; k++) spread = max(spread, maxv[k] - minv[k]);
  *outSpread = spread;
  *bx = sx / taken;
  *by = sy / taken;
  *bz = sz / taken;
  return spread < 3.0f;                 // wider than this means it was moving
}

void updateOrientation(float dt, float ax, float ay, float az,
                       float gx, float gy, float gz) {
  const float D2R = 0.0174532925f;

  float wx = (gx - biasX) * D2R;
  float wy = (gy - biasY) * D2R;
  float wz = (gz - biasZ) * D2R;

  float anorm = sqrtf(ax * ax + ay * ay + az * az);
  if (anorm > 0.5f && fabsf(anorm - 1.0f) < 0.25f) {
    float axn = ax / anorm, ayn = ay / anorm, azn = az / anorm;

    // Gravity direction the current estimate predicts, in body coordinates.
    float vx = 2.0f * (q1 * q3 - q0 * q2);
    float vy = 2.0f * (q0 * q1 + q2 * q3);
    float vz = q0 * q0 - q1 * q1 - q2 * q2 + q3 * q3;

    // Cross product = how far the estimate is from what gravity says.
    float ex = ayn * vz - azn * vy;
    float ey = azn * vx - axn * vz;
    float ez = axn * vy - ayn * vx;

    intX += KI * ex * dt;
    intY += KI * ey * dt;
    intZ += KI * ez * dt;

    wx += KP * ex + intX;
    wy += KP * ey + intY;
    wz += KP * ez + intZ;
  }

  float dq0 = 0.5f * (-q1 * wx - q2 * wy - q3 * wz);
  float dq1 = 0.5f * ( q0 * wx + q2 * wz - q3 * wy);
  float dq2 = 0.5f * ( q0 * wy - q1 * wz + q3 * wx);
  float dq3 = 0.5f * ( q0 * wz + q1 * wy - q2 * wx);

  q0 += dq0 * dt;  q1 += dq1 * dt;  q2 += dq2 * dt;  q3 += dq3 * dt;

  float n = sqrtf(q0 * q0 + q1 * q1 + q2 * q2 + q3 * q3);
  if (n > 1e-6f) { q0 /= n; q1 /= n; q2 /= n; q3 /= n; }
}

/*
 * Heading bias is invisible to the accelerometer, so it is re-learned during
 * pauses: if the probe is motionless, whatever the gyro still reads must be
 * offset, and it is folded into the bias slowly enough that a genuine slow
 * rotation is not mistaken for drift. The clamp bounds how far the learned
 * value can walk from the boot calibration, so even a long slow pan that
 * sneaks under the stillness gate cannot poison the bias permanently.
 */
void trackStillness(uint32_t now, float ax, float ay, float az,
                    float gx, float gy, float gz) {
  float rx = gx - biasX, ry = gy - biasY, rz = gz - biasZ;
  float rate = sqrtf(rx * rx + ry * ry + rz * rz);
  float anorm = sqrtf(ax * ax + ay * ay + az * az);

  bool quiet = (rate < STILL_GYRO_DPS) && (fabsf(anorm - 1.0f) < STILL_ACC_TOL);

  if (!quiet) {
    stillSince = 0;
    isStill = false;
    return;
  }
  if (stillSince == 0) stillSince = now;
  if (now - stillSince < STILL_MS) return;

  isStill = true;
  biasX = constrain(biasX + BIAS_LEARN * (gx - biasX),
                    calX - BIAS_CLAMP_DPS, calX + BIAS_CLAMP_DPS);
  biasY = constrain(biasY + BIAS_LEARN * (gy - biasY),
                    calY - BIAS_CLAMP_DPS, calY + BIAS_CLAMP_DPS);
  biasZ = constrain(biasZ + BIAS_LEARN * (gz - biasZ),
                    calZ - BIAS_CLAMP_DPS, calZ + BIAS_CLAMP_DPS);
}

void stepImu(uint32_t now) {
  float ax, ay, az, gx, gy, gz;
  if (!readImu(&ax, &ay, &az, &gx, &gy, &gz)) return;

  uint32_t nowUs = micros();
  float dt = (nowUs - lastMicros) / 1000000.0f;
  lastMicros = nowUs;
  if (dt <= 0 || dt > 0.5f) dt = IMU_PERIOD_MS / 1000.0f;

  trackStillness(now, ax, ay, az, gx, gy, gz);
  updateOrientation(dt, ax, ay, az, gx, gy, gz);

  if (++imuTick % IMU_SEND_DIV != 0) return;   // integrate 100 Hz, send 50 Hz

  char line[240];
  snprintf(line, sizeof(line),
           "{\"t\":%lu,\"q\":[%.4f,%.4f,%.4f,%.4f],"
           "\"a\":[%.3f,%.3f,%.3f],\"g\":[%.2f,%.2f,%.2f],"
           "\"b\":[%.2f,%.2f,%.2f],\"st\":%d}",
           (unsigned long)now, q0, q1, q2, q3,
           ax, ay, az, gx - biasX, gy - biasY, gz - biasZ,
           biasX, biasY, biasZ, isStill ? 1 : 0);
  sendText(TYPE_IMU, line);
}

// ---------------------------------------------------------------- lifecycle

/*
 * SENSOR PRESETS -- direct register experiments, cycled live from the Pi.
 *
 * DIAG settled the byte order: mode 0 (untouched RGB565) is spatially
 * correct, so what remains is the sensor's own colour rendering (blue
 * starved, yellow-green cast). That is decided inside the GC0308 by its
 * output-format path, AWB state, gains and Bayer phase -- all registers.
 * The esp32-camera high-level setters (set_whitebal etc.) are largely
 * no-ops for this driver, so we poke registers ourselves via set_reg().
 *
 * Presets in order of likelihood:
 *   0  RGB565 as the driver configures it (reference, = today's picture)
 *   1  sensor native YUV422, Y Cb Y Cr (0x24=0xa2) -- the manufacturer's
 *      standard path; the sensor-internal YUV->RGB565 stage is the prime
 *      suspect for the cast. Encoder fed as YUYV (colour mode 2).
 *   2  sensor YUV422, Y Cr Y Cb (0x24=0xa3) -- if preset 1 shows red and
 *      blue swapped, this is the one.
 *   3  RGB565 + AWB forced ON (0x22 bit1)
 *   4  RGB565 + AWB OFF, unity WB gains (0x5a/0x5b/0x5c = 0x40)
 *   5-8 RGB565 + mirror/flip 0x14 = 0x10/0x11/0x12/0x13 -- a flip that
 *      does not match the sensor's Bayer phase demosaics with the colour
 *      planes shifted: structure intact, colours globally wrong.
 *
 * Every switch is acknowledged with {"status":"sensor_preset","n":k,...}.
 * Once one looks natural, make it SENSOR_PRESET_BOOT and note it in
 * HANDOFF.md. All writes go to register page 0 (0xfe = 0x00 first).
 */
static const uint8_t SENSOR_PRESET_BOOT = 0;
static const uint8_t SENSOR_PRESETS = 9;
volatile uint8_t sensorPreset = SENSOR_PRESET_BOOT;

/*
 * Preset map, v4.3. DIAG under preset 1 proved two things: register writes
 * now land (the sensor really switched to YUV), and in YUV the luma is a
 * correct picture while chroma is wrong and highlights are blown out --
 * i.e. the sensor's automatics (AEC/AWB/AGC, reg 0x22 bits 0/1/2) or the
 * Cb/Cr order, not the byte path. So the presets are now organised around
 * the YUV path with the automatics as the variable, RGB565 kept as reference.
 */
static const char *presetName(uint8_t n) {
  switch (n) {
    case 0: return "rgb565 driver default";
    case 1: return "YUV YCbYCr 0xa2";
    case 2: return "YUV YCrYCb 0xa3";
    case 3: return "YUV 0xa2 + AEC/AWB/AGC on";
    case 4: return "YUV 0xa3 + AEC/AWB/AGC on";
    case 5: return "YUV 0xa2 manual: unity gains";
    case 6: return "rgb565 + AEC/AWB/AGC on";
    case 7: return "rgb565 manual: unity gains";
    case 8: return "rgb565 flip reg14=0x11";
    default: return "?";
  }
}

/*
 * DIRECT SENSOR REGISTER ACCESS THAT SURVIVES M5.begin()
 * (see the field finding in HANDOFF §2: the driver's SCCB handle dies after
 * M5.begin(); we use M5Unified's I2C on the SCCB pins and read back.)
 */
static const uint8_t GC0308_ADDR = 0x21;
static const uint32_t SCCB_HZ = 100000;
static bool sensorBusReady = false;
static int init14 = -1, init22 = -1;          // driver-default values, for the references

bool sensorBusBegin() {
  if (sensorBusReady) return true;
  M5.Ex_I2C.release();
  delay(2);
  sensorBusReady = M5.Ex_I2C.begin(I2C_NUM_0, SIOD_GPIO_NUM, SIOC_GPIO_NUM);
  return sensorBusReady;
}

bool sregWrite(uint8_t reg, uint8_t val) {
  if (!sensorBusBegin()) return false;
  return M5.Ex_I2C.writeRegister8(GC0308_ADDR, reg, val, SCCB_HZ);
}

int sregRead(uint8_t reg) {
  if (!sensorBusBegin()) return -1;
  uint8_t v = 0;
  if (!M5.Ex_I2C.readRegister(GC0308_ADDR, reg, &v, 1, SCCB_HZ)) return -1;
  return v;
}

static bool setAutomatics(bool on) {
  int v = sregRead(0x22);
  if (v < 0) return false;
  uint8_t nv = on ? (uint8_t)(v | 0x07) : (uint8_t)(v & ~0x07);   // AEC | AWB | AGC
  return sregWrite(0x22, nv);
}

static bool unityGains() {
  return sregWrite(0x5a, 0x40) && sregWrite(0x5b, 0x40) && sregWrite(0x5c, 0x40);
}

// Snapshot of the registers that decide geometry, format, exposure and colour.
// Shown on the Pi so the sensor state can be read off a photograph.
static void dumpRegs(char *out, size_t n) {
  static const uint8_t regs[] = { 0x14, 0x22, 0x24, 0x54, 0x05, 0x06, 0x07, 0x08,
                                  0x09, 0x0a, 0x03, 0x04, 0x50, 0x5a, 0x5b, 0x5c };
  size_t used = 0;
  for (size_t i = 0; i < sizeof(regs) && used + 6 < n; i++) {
    int v = sregRead(regs[i]);
    used += snprintf(out + used, n - used, "%02x=%02x ", regs[i], v < 0 ? 0xEE : v);
  }
  if (used) out[used - 1] = 0;
}

// Extended dump for the Pi's TUNE screen: everything it can adjust, plus
// the window registers for reference.
static void dumpTuneRegs(char *out, size_t n) {
  static const uint8_t regs[] = { 0x14, 0x22, 0x24, 0x03, 0x04, 0x50, 0x5a, 0x5b,
                                  0x5c, 0xb1, 0xb2, 0xb3, 0xb4, 0xb5, 0x05, 0x06,
                                  0x07, 0x08, 0x09, 0x0a };
  size_t used = 0;
  for (size_t i = 0; i < sizeof(regs) && used + 6 < n; i++) {
    int v = sregRead(regs[i]);
    used += snprintf(out + used, n - used, "%02x=%02x ", regs[i], v < 0 ? 0xEE : v);
  }
  if (used) out[used - 1] = 0;
}

void sendRegDump() {
  sregWrite(0xfe, 0x00);
  char regs[200];
  dumpTuneRegs(regs, sizeof(regs));
  char msg[260];
  snprintf(msg, sizeof(msg), "{\"status\":\"regs\",\"regs\":\"%s\"}", regs);
  sendText(TYPE_IMU, msg);
}

// One register write from the Pi (TUNE row). Page 0 unless the page register
// itself is the target; always read back so the Pi can prove it landed.
void writeRegFromPi(uint8_t reg, uint8_t val) {
  bool ok = true;
  if (reg != 0xfe) ok &= sregWrite(0xfe, 0x00);
  ok &= sregWrite(reg, val);
  int rb = sregRead(reg);
  ok &= (rb == val);
  char msg[120];
  snprintf(msg, sizeof(msg),
           "{\"status\":\"reg\",\"reg\":\"%02x\",\"val\":\"%02x\",\"rb\":\"%02x\",\"ok\":%d}",
           reg, val, rb < 0 ? 0xEE : rb, ok ? 1 : 0);
  sendText(TYPE_IMU, msg);
}

// Returns false when a write did not land (verified by readback / bus state).
bool applyPreset(uint8_t n) {
  bool yuv = (n >= 1 && n <= 5);
  uint8_t fmt = yuv ? ((n == 2 || n == 4) ? 0xa3 : 0xa2) : 0xa6;
  bool ok = sregWrite(0xfe, 0x00);                  // register page 0
  int pid = sregRead(0x00);                         // 0x9B proves the bus talks
  if (init14 < 0) { init14 = sregRead(0x14); init22 = sregRead(0x22); }

  ok &= sregWrite(0x24, fmt);
  switch (n) {
    case 0:
    case 1:
    case 2:
      if (init14 >= 0) ok &= sregWrite(0x14, (uint8_t)init14);
      if (init22 >= 0) ok &= sregWrite(0x22, (uint8_t)init22);
      break;
    case 3: case 4: case 6:
      ok &= setAutomatics(true);
      break;
    case 5: case 7:
      ok &= setAutomatics(false);
      ok &= unityGains();
      break;
    case 8:
      ok &= sregWrite(0x14, 0x11);
      break;
    default: break;
  }
  int rb24 = sregRead(0x24);
  ok &= (rb24 == fmt);

  colourMode = yuv ? 2 : 0;                        // encoder must match the bytes
  sensorPreset = n;

  char regs[160];
  dumpRegs(regs, sizeof(regs));
  char msg[320];
  snprintf(msg, sizeof(msg),
           "{\"status\":\"sensor_preset\",\"n\":%u,\"name\":\"%s\",\"ok\":%d,"
           "\"pid\":\"0x%02X\",\"reg24\":\"0x%02X\",\"cm\":%u,\"regs\":\"%s\"}",
           (unsigned)n, presetName(n), ok ? 1 : 0,
           pid < 0 ? 0 : pid, rb24 < 0 ? 0 : rb24, (unsigned)colourMode, regs);
  sendText(TYPE_IMU, msg);
  return ok;
}

/*
 * The Pi can send single command bytes on the same CDC link (v3.2+); drained
 * every loop pass so old bytes cannot pile up in the RX buffer.
 *   'n' next sensor preset, 'p' previous, 'c' colour mode, 'r' raw frame,
 *   's' raw stream, 't' test pattern.
 */
static int pendW = -1;          // -1 idle, 0 want reg, 1 want value
static uint8_t pendReg = 0;

void handleCommands() {
  while (Serial.available() > 0) {
    int c = Serial.read();
    if (pendW == 0) { pendReg = (uint8_t)c; pendW = 1; continue; }
    if (pendW == 1) { pendW = -1; writeRegFromPi(pendReg, (uint8_t)c); continue; }
    if (c == 'W') { pendW = 0; continue; }          // 'W' reg val  (3 raw bytes)
    if (c == 'g' || c == 'G') { sendRegDump(); continue; }
    if (c == 'b' || c == 'B') {
      // SENSOR-internal colour bars (GC0308 debug reg 0x2e bit0): generated
      // inside the chip, so lens, light, exposure and AWB are all out of the
      // picture. If these arrive with the wrong colours, the parallel data
      // path is at fault; if they arrive right, everything before the ISP is
      // fine and the cast is a processing problem. Toggles.
      static bool barsOn = false;
      barsOn = !barsOn;
      sregWrite(0xfe, 0x00);
      bool ok = sregWrite(0x2e, barsOn ? 0x01 : 0x00);
      int rb = sregRead(0x2e);
      char msg[96];
      snprintf(msg, sizeof(msg),
               "{\"status\":\"sensor_bars\",\"on\":%d,\"ok\":%d,\"rb\":\"%02x\"}",
               barsOn ? 1 : 0, (ok && rb == (barsOn ? 1 : 0)) ? 1 : 0, rb < 0 ? 0xEE : rb);
      sendText(TYPE_IMU, msg);
      continue;
    }
    uint8_t m = colourMode;
    if (c == 'r' || c == 'R') { rawOnce = true; continue; }
    if (c == 'n' || c == 'N') { applyPreset((sensorPreset + 1) % SENSOR_PRESETS); continue; }
    if (c == 'p' || c == 'P') { applyPreset((sensorPreset + SENSOR_PRESETS - 1) % SENSOR_PRESETS); continue; }
    if (c == 't' || c == 'T') {
      testPattern = !testPattern;
      char msg[64];
      snprintf(msg, sizeof(msg),
               "{\"status\":\"test_pattern\",\"on\":%d}", testPattern ? 1 : 0);
      sendText(TYPE_IMU, msg);
      continue;
    }
    if (c == 's' || c == 'S') {
      rawStream = !rawStream;
      char msg[64];
      snprintf(msg, sizeof(msg),
               "{\"status\":\"raw_stream\",\"on\":%d}", rawStream ? 1 : 0);
      sendText(TYPE_IMU, msg);
      continue;
    }
    if (c >= '0' && c <= '3') m = (uint8_t)(c - '0');
    else if (c == 'c' || c == 'C')        m = (uint8_t)((m + 1) % 3);
    else continue;
    if (m != colourMode) {
      colourMode = m;
      char msg[48];
      snprintf(msg, sizeof(msg),
               "{\"status\":\"colour_mode\",\"mode\":%u}", (unsigned)m);
      sendText(TYPE_IMU, msg);
    }
  }
}

void setup() {
  txMux = xSemaphoreCreateMutex();

  // Camera first: M5.begin() claims I2C port 1 and never releases it, so the
  // SCCB driver has to get its bus before that happens.
  cameraReady = startCamera();

  auto cfg = M5.config();
  cfg.external_imu = false;      // nothing on the Grove port; leave I2C 0 alone
  cfg.external_rtc = false;
  M5.begin(cfg);

  Serial.begin(115200);
  uint32_t start = millis();
  while (!Serial && millis() - start < 3000) delay(10);

  char msg[200];
  if (cameraReady) {
    sensor_t *s = esp_camera_sensor_get();
    // Production boot deliberately applies no colour tuning. A non-zero
    // preset is an explicit developer diagnostic only.
    if (SENSOR_PRESET_BOOT != 0) applyPreset(SENSOR_PRESET_BOOT);
    snprintf(msg, sizeof(msg),
             "{\"status\":\"camera_ok\",\"pid\":\"0x%04X\",\"psram\":%u,\"video\":\"official_frame2jpg\"}",
             s ? s->id.PID : 0, (unsigned)ESP.getFreePsram());
  } else {
    snprintf(msg, sizeof(msg),
             "{\"status\":\"camera_failed\",\"psram\":%u,\"hint\":\"%s\"}",
             (unsigned)ESP.getFreePsram(),
             ESP.getPsramSize() == 0 ? "select board M5AtomS3R" : "unknown");
  }
  sendText(TYPE_IMU, msg);

  // The BMI270 needs a moment after M5.begin() before it returns samples.
  float tx, ty, tz, dx, dy, dz;
  bool imuOk = false;
  for (int i = 0; i < 30 && !imuOk; i++) {
    imuOk = readImu(&tx, &ty, &tz, &dx, &dy, &dz);
    if (!imuOk) delay(25);
  }
  sendText(TYPE_IMU, imuOk ? "{\"status\":\"imu_ok\"}"
                           : "{\"status\":\"imu_unavailable\"}");

  if (imuOk) {
    // Up to three attempts: a probe still swinging from being plugged in
    // fails the first pass and settles for the second. A calibration that
    // saw movement is discarded -- installing it (as v2 did) bakes the
    // movement into every heading afterwards. Zero plus the runtime
    // stillness learning beats a poisoned average.
    float bx, by, bz, spread = 0;
    bool alive = true;
    for (uint8_t attempt = 1; attempt <= CALIB_TRIES && alive; attempt++) {
      snprintf(msg, sizeof(msg),
               "{\"status\":\"calibrating\",\"attempt\":%u,\"hold_still_ms\":1500}",
               attempt);
      sendText(TYPE_IMU, msg);
      delay(200);
      calibOk = measureGyroBias(&bx, &by, &bz, &spread, &alive);
      if (calibOk) {
        biasX = calX = bx;  biasY = calY = by;  biasZ = calZ = bz;
      }
      snprintf(msg, sizeof(msg),
               "{\"status\":\"%s\",\"bias\":[%.2f,%.2f,%.2f],\"spread\":%.2f,"
               "\"attempt\":%u}",
               calibOk ? "calibrated" : "calib_moved",
               biasX, biasY, biasZ, spread, attempt);
      sendText(TYPE_IMU, msg);
      if (calibOk) break;
    }

    // Seed the orientation from gravity so the filter does not have to swing
    // in from an arbitrary starting attitude.
    if (readImu(&tx, &ty, &tz, &dx, &dy, &dz)) {
      float n = sqrtf(tx * tx + ty * ty + tz * tz);
      if (n > 0.5f) {
        tx /= n; ty /= n; tz /= n;
        float pitch = atan2f(-tx, sqrtf(ty * ty + tz * tz));
        float roll  = atan2f(ty, tz);
        float cp = cosf(pitch * 0.5f), sp = sinf(pitch * 0.5f);
        float cr = cosf(roll  * 0.5f), sr = sinf(roll  * 0.5f);
        q0 = cr * cp;  q1 = sr * cp;  q2 = cr * sp;  q3 = -sr * sp;
      }
    }
  }

  {
    // Keep generous headroom: the old 64-byte buffer truncated this JSON and
    // made the Pi silently miss the firmware version/ready state.
    char rdy[128];
    snprintf(rdy, sizeof(rdy),
             "{\"status\":\"ready\",\"v\":4,\"r\":0,\"cm\":%u,\"calib\":%d,\"raw\":%d,\"sp\":%u}",
             (unsigned)colourMode, calibOk ? 1 : 0, rawStream ? 1 : 0,
             (unsigned)sensorPreset);
    sendText(TYPE_IMU, rdy);
  }
  if (cameraReady) sendRegDump();          // driver defaults, before any TUNE write
  lastMicros = micros();

#if USE_CAMERA_TASK
  if (cameraReady) {
    xTaskCreatePinnedToCore(camTask, "cam", 12288, NULL, 1, NULL, 0);
  }
#endif
}

void loop() {
  M5.update();
  handleCommands();

  uint32_t now = millis();

  if (now - lastImu >= IMU_PERIOD_MS) {
    lastImu = now;
    stepImu(now);
  }

#if !USE_CAMERA_TASK
  static uint32_t lastFrame = 0;
  if (cameraReady && now - lastFrame >= FRAME_PERIOD_MS) {
    lastFrame = now;
    sendFrame();
  }
#endif

  delay(1);          // yield; the IMU cadence is millis-gated above
}
