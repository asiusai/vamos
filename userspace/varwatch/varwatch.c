#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/statvfs.h>
#include <time.h>
#include <unistd.h>

static int parse_uint(const char *value, unsigned int fallback) {
  char *end = NULL;
  unsigned long parsed = strtoul(value, &end, 10);
  return (end != value && *end == '\0' && parsed <= 100000U) ? (int)parsed : (int)fallback;
}

static void truncate_regular_logs(const char *path) {
  DIR *dir = opendir(path);
  if (dir == NULL) {
    fprintf(stderr, "vamos-varwatch: cannot open %s: %s\n", path, strerror(errno));
    return;
  }

  int directory_fd = dirfd(dir);
  struct dirent *entry;
  while ((entry = readdir(dir)) != NULL) {
    if (entry->d_name[0] == '.' &&
        (entry->d_name[1] == '\0' || (entry->d_name[1] == '.' && entry->d_name[2] == '\0'))) {
      continue;
    }

    struct stat info;
    if (fstatat(directory_fd, entry->d_name, &info, AT_SYMLINK_NOFOLLOW) != 0 || !S_ISREG(info.st_mode)) {
      continue;
    }

    int fd = openat(directory_fd, entry->d_name, O_WRONLY | O_TRUNC | O_CLOEXEC | O_NOFOLLOW);
    if (fd >= 0) {
      close(fd);
    }
  }
  closedir(dir);
}

int main(int argc, char **argv) {
  const char *path = argc > 1 ? argv[1] : "/var/log";
  unsigned int threshold = (unsigned int)(argc > 2 ? parse_uint(argv[2], 70) : 70);
  unsigned int interval = (unsigned int)(argc > 3 ? parse_uint(argv[3], 5) : 5);
  if (threshold > 100 || interval == 0) {
    fprintf(stderr, "usage: %s [path [threshold-percent [interval-seconds]]]\n", argv[0]);
    return 2;
  }

  const struct timespec delay = {.tv_sec = interval, .tv_nsec = 0};
  while (1) {
    struct statvfs usage;
    if (statvfs(path, &usage) == 0 && usage.f_blocks != 0) {
      unsigned long long used = usage.f_blocks - usage.f_bavail;
      if (used * 100ULL > (unsigned long long)threshold * usage.f_blocks) {
        fprintf(stderr, "vamos-varwatch: %s exceeded %u%%; truncating top-level logs\n", path, threshold);
        truncate_regular_logs(path);
      }
    }
    nanosleep(&delay, NULL);
  }
}
