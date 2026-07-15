#define _GNU_SOURCE
#include <errno.h>
#include <limits.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/statvfs.h>

static int mount_entry(const char *wanted_mount, const char *wanted_root) {
    FILE *stream = fopen("/proc/self/mountinfo", "re");
    char line[8192], root[PATH_MAX], mountpoint[PATH_MAX], options[1024];
    int found = 0;

    if (stream == NULL) {
        fprintf(stderr, "cannot read mountinfo: %s\n", strerror(errno));
        return 0;
    }
    while (fgets(line, sizeof line, stream) != NULL) {
        if (sscanf(line, "%*u %*u %*s %4095s %4095s %1023s", root, mountpoint, options) != 3) {
            continue;
        }
        if (strcmp(mountpoint, wanted_mount) == 0 &&
            (wanted_root == NULL || strcmp(root, wanted_root) == 0)) {
            found = 1;
            break;
        }
    }
    fclose(stream);
    return found;
}

int main(void) {
    const char *store = "/mnt/ai-store";
    const char *source = "/mnt/ai-store/foothills-project/00-inbox";
    const char *target = "/Foothills-Inbox";
    struct stat store_stat, source_stat, target_stat;
    struct statvfs target_vfs;

    if (!mount_entry(store, NULL)) {
        fputs("AI Store is not a mountpoint\n", stderr);
        return 20;
    }
    if (!mount_entry(target, "/foothills-project/00-inbox")) {
        fputs("Foothills Inbox is not the expected bind mount\n", stderr);
        return 21;
    }
    if (stat(store, &store_stat) != 0 || stat(source, &source_stat) != 0 ||
        stat(target, &target_stat) != 0) {
        fprintf(stderr, "cannot stat Foothills Inbox paths: %s\n", strerror(errno));
        return 22;
    }
    if (store_stat.st_dev != source_stat.st_dev ||
        source_stat.st_dev != target_stat.st_dev ||
        source_stat.st_ino != target_stat.st_ino) {
        fputs("Foothills Inbox source and target identities do not match\n", stderr);
        return 23;
    }
    if (statvfs(target, &target_vfs) != 0) {
        fprintf(stderr, "cannot inspect Foothills Inbox filesystem: %s\n", strerror(errno));
        return 24;
    }
    if ((target_vfs.f_flag & ST_RDONLY) != 0) {
        fputs("Foothills Inbox filesystem is read-only\n", stderr);
        return 25;
    }
    return 0;
}
