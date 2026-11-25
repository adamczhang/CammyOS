/* SPDX-License-Identifier: MIT
 * Local development helper: detach a command before its ADB shell exits.
 * No network interface and no privileged operation beyond the caller's UID.
 */
#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>

int main(int argc, char **argv) {
    int ack[2];
    if (argc < 3) { fprintf(stderr, "usage: daemon-exec LOG COMMAND [ARGS...]\n"); return 2; }
    if (pipe(ack) < 0) { perror("pipe"); return 1; }
    pid_t child = fork();
    if (child < 0) { perror("fork"); return 1; }
    if (child > 0) {
        close(ack[1]);
        pid_t daemon_pid = 0;
        ssize_t n = read(ack[0], &daemon_pid, sizeof daemon_pid);
        close(ack[0]);
        (void)waitpid(child, NULL, 0);
        if (n != (ssize_t)sizeof daemon_pid || daemon_pid <= 0) return 1;
        printf("Detached PID %ld\n", (long)daemon_pid);
        return 0;
    }
    close(ack[0]);
    if (setsid() < 0) _exit(1);
    (void)signal(SIGHUP, SIG_IGN);
    pid_t daemon_pid = fork();
    if (daemon_pid < 0) _exit(1);
    if (daemon_pid > 0) {
        if (write(ack[1], &daemon_pid, sizeof daemon_pid) != (ssize_t)sizeof daemon_pid) _exit(1);
        close(ack[1]);
        _exit(0);
    }
    close(ack[1]);
    int nullfd = open("/dev/null", O_RDWR);
    int logfd = open(argv[1], O_WRONLY | O_CREAT | O_APPEND, 0600);
    if (nullfd < 0 || logfd < 0) _exit(1);
    if (dup2(nullfd, 0) < 0 || dup2(logfd, 1) < 0 || dup2(logfd, 2) < 0) _exit(1);
    if (nullfd > 2) close(nullfd);
    if (logfd > 2) close(logfd);
    execvp(argv[2], &argv[2]);
    perror("execvp");
    _exit(127);
}
