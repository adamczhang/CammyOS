/* SPDX-License-Identifier: MIT */
#ifndef CAMMY_PARAMFACE_H
#define CAMMY_PARAMFACE_H
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
void *cm_pf_create(int width,int height);
void cm_pf_destroy(void *ctx);
/* JSON must first pass the service's bounded schema admission. */
int cm_pf_load(void *ctx,const char *json,size_t length);
int cm_pf_expression(void *ctx,int expression);
int cm_pf_drive(void *ctx,int mouth_enabled,float mouth,int gaze_enabled,float h,float v);
int cm_pf_frame(void *ctx,float dt_ms,uint8_t *rgb,size_t capacity);
const char *cm_pf_error(void *ctx);
#ifdef __cplusplus
}
#endif
#endif
