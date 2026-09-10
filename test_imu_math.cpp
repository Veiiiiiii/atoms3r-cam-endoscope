// SPDX-License-Identifier: MIT
// Includes PRODUCTION math; no duplicate filter implementation.
#include "firmware/main/service/imu_math.h"
#include <cassert>
#include <iostream>
using namespace imu_math;
int main() {
    const Vec3 gravity{0,0,1}, bias{0.1f,-0.2f,0.05f};
    GyroCalibration calibration;
    for (int n=0;n<299;++n) assert(!calibration.add(bias,gravity));
    assert(calibration.add(bias,gravity));
    assert(fabsf(calibration.bias().y-bias.y)<1e-5f);
    GyroCalibration moving;
    for (int n=0;n<1000;++n) assert(!moving.add({0,0,2},gravity));
    GyroCalibration tilt;
    for (int n=0;n<1000;++n) {
        float a=n*0.0005f;
        assert(!tilt.add({}, {sinf(a),0,cosf(a)}));
    }
    StillDetector still;
    for (int n=0;n<200;++n) assert(!still.update(gravity,{},n*10000));
    assert(still.update(gravity,{},2000000));
    assert(!still.update(gravity,{0,0,0.5f},2010000));
    for (int n=0;n<1000;++n) {
        float a=n*0.0001f;
        assert(!still.update({sinf(a),0,cosf(a)},{},3000000+n*10000));
    }
    // Timing gaps cannot count as continuous measured stillness.
    still.reset(); assert(!still.update(gravity,{},0));
    assert(!still.update(gravity,{},3000000));
    MahonyFusion fusion;
    fusion.seed_from_gravity(gravity);
    for (int n=0;n<100;++n) fusion.update(gravity,{0,0,90},0.01f);
    assert(fabsf(fusion.quaternion()[0]-sqrtf(0.5f))<0.001f);
    for (int n=0;n<100;++n) fusion.update(gravity,{0,0,-90},0.01f);
    assert(fabsf(fusion.quaternion()[0]-1)<0.0001f);
    // Strong translation must not drag attitude towards a false gravity vector.
    for (int n=0;n<100;++n) fusion.update({2,0,1},{},0.01f);
    assert(fabsf(fusion.quaternion()[2])<0.0001f);
    std::cout << "PASS: production calibration, stillness, timing, fusion\n";
}
