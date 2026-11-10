/* Keep the host network namespace without inheriting its abstract Unix sockets
 * or permission to signal desktop processes. Filesystem isolation is bwrap's.
 * ABI 6 is mandatory: an older kernel must not silently omit these boundaries.
 */
#define _GNU_SOURCE
#include <linux/landlock.h>
#include <seccomp.h>
#include <sys/prctl.h>
#include <sys/syscall.h>
#include <unistd.h>
#include <stdio.h>
#include <errno.h>
#include <string.h>

int main(int argc, char **argv)
{
    int abi = syscall(SYS_landlock_create_ruleset, NULL, 0,
                      LANDLOCK_CREATE_RULESET_VERSION);
    if (abi < 6) {
        fputs("Firefox requires Landlock ABI 6 or newer\n", stderr);
        return 1;
    }
    if (argc == 2 && strcmp(argv[1], "--check") == 0)
        return 0;
    if (argc < 2)
        return 1;
    struct landlock_ruleset_attr rules = {
        .scoped = LANDLOCK_SCOPE_ABSTRACT_UNIX_SOCKET | LANDLOCK_SCOPE_SIGNAL,
    };
    int fd = syscall(SYS_landlock_create_ruleset, &rules, sizeof(rules), 0);
    if (fd < 0 || prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) ||
        syscall(SYS_landlock_restrict_self, fd, 0)) {
        perror("Firefox Landlock confinement");
        return 1;
    }
    close(fd);
    /* The inherited desktop keyrings are not a browser permission. Also close
     * inspection/handle and kernel interfaces that this package does not use.
     * Keep clone/unshare/chroot/seccomp available to Gecko's nested sandboxes.
     */
    const char *blocked[] = {
        "keyctl", "add_key", "request_key", "ptrace", "process_vm_readv",
        "process_vm_writev", "kcmp", "perf_event_open", "bpf", "userfaultfd",
        "io_uring_setup", "io_uring_register", "io_uring_enter",
        "open_by_handle_at", "name_to_handle_at",
    };
    scmp_filter_ctx filter = seccomp_init(SCMP_ACT_ALLOW);
    if (!filter)
        return 1;
    for (size_t i = 0; i < sizeof(blocked) / sizeof(blocked[0]); i++) {
        int nr = seccomp_syscall_resolve_name(blocked[i]);
        if (nr == __NR_SCMP_ERROR ||
            seccomp_rule_add(filter, SCMP_ACT_ERRNO(EPERM), nr, 0)) {
            fputs("Firefox syscall policy setup failed\n", stderr);
            seccomp_release(filter);
            return 1;
        }
    }
    if (seccomp_load(filter)) {
        fputs("Firefox syscall policy installation failed\n", stderr);
        seccomp_release(filter);
        return 1;
    }
    seccomp_release(filter);
    execvp(argv[1], argv + 1);
    perror("Firefox sandbox exec");
    return 1;
}
