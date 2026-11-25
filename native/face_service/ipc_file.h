/* SPDX-License-Identifier: MIT */
#ifndef CAMMY_FACE_IPC_FILE_H
#define CAMMY_FACE_IPC_FILE_H
#include "face_service.h"
#define CFS_DIRECTORY "/tmp/cammy-face-service"
#define CFS_FILE CFS_DIRECTORY "/control"
int cfs_current_boot(uint32_t[4]);
int cfs_map_file(const char *,int,struct cfs_page **);
#endif
