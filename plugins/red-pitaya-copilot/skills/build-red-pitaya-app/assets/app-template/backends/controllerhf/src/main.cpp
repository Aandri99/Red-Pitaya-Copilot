#include "controller_contract.h"

#include <cstdlib>

namespace {
rp_app_params_t parameters[] = {
    {"APP_VERSION", 1.0F, 0, 1, 1.0F, 1.0F},
    {"SAFE_STATE", 1.0F, 0, 1, 0.0F, 1.0F},
};
constexpr int parameter_count =
    static_cast<int>(sizeof(parameters) / sizeof(parameters[0]));
}

extern "C" {

const char *rp_app_desc(void) {
    return "__APP_TITLE__ native controller scaffold";
}

int rp_app_init(void) {
    // Establish a confirmed safe state before reporting successful startup.
    parameters[1].value = 1.0F;
    return 0;
}

int rp_app_exit(void) {
    // Keep shutdown idempotent and disable physical output here.
    parameters[1].value = 1.0F;
    return 0;
}

int rp_set_params(rp_app_params_t *incoming, int length) {
    if (incoming == nullptr || length < 0) return -1;
    // Read-only scaffold: reject mutations until reconciled with target ABI.
    return length == 0 ? 0 : -1;
}

int rp_get_params(rp_app_params_t **out) {
    if (out == nullptr) return -1;
    *out = parameters;
    return parameter_count;
}

int rp_get_signals(float ***signals, int *signal_count, int *signal_length) {
    if (signals == nullptr || signal_count == nullptr || signal_length == nullptr) {
        return -1;
    }
    *signals = nullptr;
    *signal_count = 0;
    *signal_length = 0;
    return 0;
}

void UpdateSignals(void) {}
void UpdateParams(void) {}
void OnNewParams(void) {}
void OnNewSignals(void) {}
void PostUpdateSignals(void) {}

}  // extern "C"
