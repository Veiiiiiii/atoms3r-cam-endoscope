// Screen-mounted IMU telemetry for Seeed XIAO nRF52840 Sense.
// USB CDC is the tested/default transport. Uncomment for the optional,
// untested D6/TX hardware-UART duplicate instead:
// #define USE_UART1

#include <Arduino.h>
#include <Wire.h>
#include <LSM6DS3.h>

namespace {

constexpr uint8_t kImuAddress = 0x6A;
constexpr uint8_t kVersion = 1;
constexpr uint8_t kFlagValid = 1u << 0;
constexpr uint32_t kBaud = 115200;
constexpr uint32_t kSampleRateHz = 100;
constexpr uint32_t kSamplePeriodUs = 1000000UL / kSampleRateHz;

LSM6DS3 imu(I2C_MODE, kImuAddress);
bool imu_ready = false;
uint32_t sequence_number = 0;
uint64_t next_sample_us = 0;

struct __attribute__((packed)) ScreenImuRecordV1 {
  char magic[4];              // "SIMU"
  uint8_t version;
  uint8_t flags;
  uint16_t packet_size;
  uint32_t sequence;
  uint64_t timestamp_us;
  float accel_g[3];           // native LSM6DS3TR-C X/Y/Z, +/-4 g
  float gyro_dps[3];          // native LSM6DS3TR-C X/Y/Z, +/-500 dps
  uint32_t crc32;
};

static_assert(sizeof(ScreenImuRecordV1) == 48,
              "SIMU v1 record must stay exactly 48 bytes");

uint64_t extended_micros() {
  // Arduino micros() is 32-bit on this board. Extend it so a running XIAO
  // does not look as if it rebooted at the approximately 71.6 minute wrap.
  static uint32_t previous_low = 0;
  static uint64_t high = 0;
  const uint32_t low = micros();
  if (low < previous_low) {
    high += (UINT64_C(1) << 32);
  }
  previous_low = low;
  return high | static_cast<uint64_t>(low);
}

uint32_t ieee_crc32(const uint8_t *data, size_t length) {
  uint32_t crc = 0xFFFFFFFFu;
  while (length-- != 0) {
    crc ^= *data++;
    for (uint8_t bit = 0; bit < 8; ++bit) {
      crc = (crc >> 1) ^ (0xEDB88320u &
                           static_cast<uint32_t>(-(static_cast<int32_t>(crc & 1u))));
    }
  }
  return ~crc;
}

int16_t little_endian_i16(const uint8_t *p) {
  return static_cast<int16_t>(static_cast<uint16_t>(p[0]) |
                              (static_cast<uint16_t>(p[1]) << 8));
}

bool read_sample(float accel_g[3], float gyro_dps[3]) {
  // OUTX_L_G..OUTZ_H_XL is one coherent 12-byte auto-increment read through
  // Seeed Arduino LSM6DS3's checked low-level API.
  uint8_t raw[12] = {};
  if (!imu_ready || imu.readRegisterRegion(
          raw, LSM6DS3_ACC_GYRO_OUTX_L_G, sizeof(raw)) != IMU_SUCCESS) {
    return false;
  }

  // LSM6DS3TR-C sensitivities: 17.50 mdps/LSB at +/-500 dps and
  // 0.122 mg/LSB at +/-4 g. Packet axes are never remapped.
  constexpr float kGyroDpsPerLsb = 0.01750f;
  constexpr float kAccelGPerLsb = 0.000122f;
  for (uint8_t axis = 0; axis < 3; ++axis) {
    gyro_dps[axis] = little_endian_i16(&raw[axis * 2]) * kGyroDpsPerLsb;
    accel_g[axis] = little_endian_i16(&raw[6 + axis * 2]) * kAccelGPerLsb;
  }
  return true;
}

void write_record(const ScreenImuRecordV1 &rec) {
#ifdef USE_UART1
  Serial1.write(reinterpret_cast<const uint8_t *>(&rec), 48);
#else
  Serial.write(reinterpret_cast<const uint8_t *>(&rec), 48);
#endif
}

}  // namespace

void setup() {
#ifdef USE_UART1
  Serial1.begin(kBaud);
#else
  Serial.begin(kBaud);  // USB CDC; baud is conventional and ignored by USB.
#endif

  // The XIAO nRF52840 Sense gates the LSM6DS3TR-C on the P1.08 power rail.
  // Most Seeed core versions enable it via the board variant, but not all --
  // enable it explicitly so a running sketch is never left with a dead IMU.
  // Harmless if the rail is already powered; the macro exists only on the
  // Sense variant, so the block compiles away on other boards.
#ifdef PIN_LSM6DS3TR_C_POWER
  pinMode(PIN_LSM6DS3TR_C_POWER, OUTPUT);
  digitalWrite(PIN_LSM6DS3TR_C_POWER, HIGH);
  delay(10);
#endif

  // Configure range/ODR before begin(), because the library applies
  // SensorSettings there.
  imu.settings.accelRange = 4;          // +/-4 g
  imu.settings.accelSampleRate = 104;   // 104 Hz ODR
  imu.settings.gyroRange = 500;         // +/-500 dps
  imu.settings.gyroSampleRate = 104;    // 104 Hz ODR
  imu_ready = (imu.begin() == IMU_SUCCESS);
  next_sample_us = extended_micros();
}

void loop() {
  const uint64_t now_us = extended_micros();
  if (now_us < next_sample_us) {
    return;
  }
  next_sample_us += kSamplePeriodUs;
  if (now_us > next_sample_us &&
      now_us - next_sample_us > kSamplePeriodUs * 4ULL) {
    next_sample_us = now_us + kSamplePeriodUs;
  }

  ScreenImuRecordV1 rec = {};
  rec.magic[0] = 'S';
  rec.magic[1] = 'I';
  rec.magic[2] = 'M';
  rec.magic[3] = 'U';
  rec.version = kVersion;
  rec.packet_size = sizeof(rec);
  rec.sequence = sequence_number++;
  rec.timestamp_us = now_us;
  if (read_sample(rec.accel_g, rec.gyro_dps)) {
    rec.flags |= kFlagValid;
  }
  // On an I2C/read failure the zero-filled record is still emitted with
  // VALID clear. The host ignores it and its device-time clock does not move.
  rec.crc32 = ieee_crc32(reinterpret_cast<const uint8_t *>(&rec),
                         sizeof(rec) - sizeof(rec.crc32));
  write_record(rec);
}
