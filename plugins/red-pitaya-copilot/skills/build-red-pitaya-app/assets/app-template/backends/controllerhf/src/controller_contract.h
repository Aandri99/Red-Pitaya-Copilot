#pragma once

// Legacy web-controller ABI shape used by Red Pitaya examples.
// Reconcile this definition and every exported symbol with a canonical app from
// the exact target OS image before producing controllerhf.so.
typedef struct rp_app_params_s {
    const char *name;
    float value;
    int fpga_update;
    int read_only;
    float min_val;
    float max_val;
} rp_app_params_t;
