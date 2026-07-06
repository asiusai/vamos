// SPDX-License-Identifier: MIT
// Standalone Dragon RDI probe: route a selected camera PHY -> CSID -> VFE RDI,
// optionally write an OS04C10 init table, stream the video node, then start
// OS04C10 over plain i2ctransfer.

#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <linux/media.h>
#include <linux/media-bus-format.h>
#include <linux/v4l2-subdev.h>
#include <linux/videodev2.h>
#include <stdbool.h>
#include <poll.h>
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <unistd.h>

#define ARRAY_SIZE(x) (sizeof(x) / sizeof((x)[0]))

struct buffer {
  void *start;
  size_t length;
};

struct reg_entry {
  unsigned int reg;
  unsigned int value;
  unsigned int delay_ms;
  bool is_delay;
};

struct sensor_ioctl_reg {
  uint16_t addr;
  uint16_t data;
};

struct sensor_ioctl_cmd {
  uint64_t regs;
  uint32_t count;
  uint8_t data_width;
  uint8_t pad[3];
};

#define SENSOR_WRITE_REGS _IOW('S', 1, struct sensor_ioctl_cmd)

struct devmem_entry {
  unsigned long addr;
  uint32_t value;
};

struct devmem_read_entry {
  unsigned long addr;
};

static int i2c_bus = 20;
static int i2c_addr = 0x36;
static const char *i2c_dev = "20-0036";
static bool expected_set[65536];
static unsigned int expected_value[65536];

static const unsigned int readback_regs[] = {
  0x0100, 0x300a, 0x300b, 0x300c,
  0x0301, 0x0303, 0x0305, 0x0307,
  0x3012, 0x3013, 0x3016, 0x3021,
  0x3501, 0x3502, 0x3503,
  0x3808, 0x3809, 0x380a, 0x380b,
  0x380c, 0x380d, 0x380e, 0x380f,
  0x4300, 0x4302, 0x4305,
  0x4803, 0x4809, 0x480a, 0x480c, 0x480e, 0x4837,
  0x5000, 0x5040, 0x4741,
};

static unsigned int fourcc(char a, char b, char c, char d) {
  return (unsigned int)a | ((unsigned int)b << 8) |
         ((unsigned int)c << 16) | ((unsigned int)d << 24);
}

static int read_sysfs_name(const char *path, char *name, size_t name_sz) {
  FILE *fp = fopen(path, "r");
  if (!fp)
    return -1;

  if (!fgets(name, name_sz, fp)) {
    fclose(fp);
    return -1;
  }
  fclose(fp);

  name[strcspn(name, "\r\n")] = '\0';
  return 0;
}

static int find_v4l_dev_by_name(const char *prefix, const char *name,
                                char *path, size_t path_sz) {
  for (int i = 0; i < 128; i++) {
    char sysfs_path[128];
    char dev_name[128];
    snprintf(sysfs_path, sizeof(sysfs_path),
             "/sys/class/video4linux/%s%d/name", prefix, i);
    if (read_sysfs_name(sysfs_path, dev_name, sizeof(dev_name)) == 0 &&
        !strcmp(dev_name, name)) {
      snprintf(path, path_sz, "/dev/%s%d", prefix, i);
      return 0;
    }
  }

  return -1;
}

static unsigned int find_media_entity_by_name(int media_fd, const char *name,
                                              unsigned int fallback) {
  struct media_entity_desc ent = {};
  ent.id = MEDIA_ENT_ID_FLAG_NEXT;
  while (ioctl(media_fd, MEDIA_IOC_ENUM_ENTITIES, &ent) == 0) {
    if (!strcmp(ent.name, name))
      return ent.id;
    ent.id |= MEDIA_ENT_ID_FLAG_NEXT;
  }

  fprintf(stderr, "media entity '%s' not found, using fallback id %u\n",
          name, fallback);
  return fallback;
}

static void print_fourcc(unsigned int fmt) {
  printf("%c%c%c%c", fmt & 0xff, (fmt >> 8) & 0xff,
         (fmt >> 16) & 0xff, (fmt >> 24) & 0xff);
}

static int xioctl(int fd, unsigned long req, void *arg, const char *name) {
  int ret;
  do {
    ret = ioctl(fd, req, arg);
  } while (ret < 0 && errno == EINTR);
  if (ret < 0)
    fprintf(stderr, "%s failed: %s\n", name, strerror(errno));
  return ret;
}

static char *trim(char *s) {
  while (isspace((unsigned char)*s))
    s++;
  if (!*s)
    return s;
  char *end = s + strlen(s) - 1;
  while (end > s && isspace((unsigned char)*end))
    *end-- = '\0';
  return s;
}

static bool is_delay_key(const char *key) {
  return !strcasecmp(key, "delay") ||
         !strcasecmp(key, "delay_ms") ||
         !strcasecmp(key, "sleep") ||
         !strcasecmp(key, "msleep");
}

static int parse_reg_line(char *line, struct reg_entry *entry) {
  char *comment = strchr(line, '#');
  if (comment)
    *comment = '\0';

  char *s = trim(line);
  if (!*s)
    return 0;

  char *key = s;
  char *value_s = NULL;
  char *sep = strchr(s, '=');
  if (!sep)
    sep = strchr(s, ':');
  if (sep) {
    *sep = '\0';
    value_s = sep + 1;
  } else {
    value_s = s;
    while (*value_s && !isspace((unsigned char)*value_s))
      value_s++;
    if (!*value_s)
      return -1;
    *value_s++ = '\0';
  }

  key = trim(key);
  value_s = trim(value_s);
  if (!*key || !*value_s)
    return -1;

  if (is_delay_key(key)) {
    entry->is_delay = true;
    entry->delay_ms = strtoul(value_s, NULL, 0);
    return 1;
  }

  entry->is_delay = false;
  entry->reg = strtoul(key, NULL, 0) & 0xffff;
  entry->value = strtoul(value_s, NULL, 0) & 0xff;
  return 1;
}

static int parse_devmem_arg(char *arg, struct devmem_entry *entry) {
  char *sep = strchr(arg, '=');
  if (!sep)
    sep = strchr(arg, ':');
  if (!sep)
    return -1;

  *sep = '\0';
  char *addr = trim(arg);
  char *value = trim(sep + 1);
  if (!*addr || !*value)
    return -1;

  entry->addr = strtoul(addr, NULL, 0);
  entry->value = (uint32_t)strtoul(value, NULL, 0);
  return 0;
}

static int load_reg_file(const char *path, struct reg_entry **entries_out,
                         size_t *count_out, int limit) {
  FILE *fp = fopen(path, "r");
  if (!fp) {
    perror(path);
    return -1;
  }

  size_t cap = 512;
  size_t count = 0;
  struct reg_entry *entries = calloc(cap, sizeof(*entries));
  if (!entries) {
    fclose(fp);
    return -1;
  }

  char line[256];
  int line_no = 0;
  while (fgets(line, sizeof(line), fp)) {
    line_no++;
    struct reg_entry entry = {};
    int ret = parse_reg_line(line, &entry);
    if (ret < 0) {
      fprintf(stderr, "%s:%d: malformed register line\n", path, line_no);
      free(entries);
      fclose(fp);
      return -1;
    }
    if (ret == 0)
      continue;
    if (limit >= 0 && (int)count >= limit)
      break;
    if (count == cap) {
      cap *= 2;
      struct reg_entry *new_entries = realloc(entries, cap * sizeof(*entries));
      if (!new_entries) {
        free(entries);
        fclose(fp);
        return -1;
      }
      entries = new_entries;
    }
    entries[count++] = entry;
  }

  fclose(fp);
  *entries_out = entries;
  *count_out = count;
  return 0;
}

static int i2c_write_reg(unsigned int reg, unsigned int value) {
  char cmd[192];
  snprintf(cmd, sizeof(cmd),
           "i2ctransfer -f -y %d w3@0x%02x 0x%02x 0x%02x 0x%02x >/dev/null 2>&1",
           i2c_bus, i2c_addr, (reg >> 8) & 0xff, reg & 0xff, value & 0xff);
  return system(cmd);
}

static int i2c_read_reg(unsigned int reg, unsigned int *value) {
  char cmd[192];
  snprintf(cmd, sizeof(cmd),
           "i2ctransfer -f -y %d w2@0x%02x 0x%02x 0x%02x r1 2>/dev/null",
           i2c_bus, i2c_addr, (reg >> 8) & 0xff, reg & 0xff);
  FILE *fp = popen(cmd, "r");
  if (!fp)
    return -1;

  char out[64] = {};
  if (!fgets(out, sizeof(out), fp)) {
    pclose(fp);
    return -1;
  }
  int status = pclose(fp);
  if (status != 0)
    return -1;

  *value = strtoul(out, NULL, 0) & 0xff;
  return 0;
}

static void force_sensor_runtime_power_on(const char *dev) {
  char path[128];
  snprintf(path, sizeof(path), "/sys/bus/i2c/devices/%s/power/control", dev);

  int fd = open(path, O_WRONLY);
  if (fd < 0) {
    fprintf(stderr, "warning: %s: %s\n", path, strerror(errno));
    return;
  }

  if (write(fd, "on", 2) == 2)
    printf("runtime PM forced on via %s\n", path);
  else
    fprintf(stderr, "warning: failed to write %s: %s\n", path, strerror(errno));
  close(fd);
}

static int wake_sensor_subdev(int fd) {
  if (fd < 0)
    return -1;

  struct sensor_ioctl_reg reg = {
    .addr = 0x0100,
    .data = 0x00,
  };
  struct sensor_ioctl_cmd cmd = {
    .regs = (uint64_t)(uintptr_t)&reg,
    .count = 1,
    .data_width = 1,
  };

  int ret = xioctl(fd, SENSOR_WRITE_REGS, &cmd, "SENSOR_WRITE_REGS");
  if (ret == 0) {
    printf("sensor subdev wake write ok: 0x0100=0x00\n");
    usleep(100000);
  }
  return ret;
}

static int write_reg_entries(const struct reg_entry *entries, size_t count) {
  printf("writing %zu sensor init entries on i2c-%d addr=0x%02x\n",
         count, i2c_bus, i2c_addr);
  for (size_t i = 0; i < count; i++) {
    if (entries[i].is_delay) {
      printf("  delay %u ms\n", entries[i].delay_ms);
      usleep(entries[i].delay_ms * 1000);
      continue;
    }
    if (i2c_write_reg(entries[i].reg, entries[i].value) != 0) {
      fprintf(stderr, "sensor write failed at entry %zu: 0x%04x=0x%02x\n",
              i, entries[i].reg, entries[i].value);
      return -1;
    }
    if (entries[i].reg == 0x0103)
      usleep(5000);

    expected_set[entries[i].reg] = true;
    expected_value[entries[i].reg] = entries[i].value & 0xff;
  }
  expected_set[0x300a] = true;
  expected_value[0x300a] = 0x53;
  expected_set[0x300b] = true;
  expected_value[0x300b] = 0x04;
  expected_set[0x300c] = true;
  expected_value[0x300c] = 0x43;
  return 0;
}

static int sensor_readback(const char *label) {
  int mismatches = 0;
  printf("%s\n", label);
  for (unsigned int i = 0; i < ARRAY_SIZE(readback_regs); i++) {
    unsigned int reg = readback_regs[i];
    unsigned int value = 0;
    if (i2c_read_reg(reg, &value) != 0) {
      printf("  %04x=ERR\n", reg);
      mismatches++;
      continue;
    }
    printf("  %04x=0x%02x", reg, value);
    if (expected_set[reg]) {
      bool ok = value == expected_value[reg];
      printf(" expected=0x%02x %s", expected_value[reg], ok ? "ok" : "MISMATCH");
      if (!ok)
        mismatches++;
    }
    printf("\n");
  }
  printf("%s mismatches=%d\n", label, mismatches);
  return mismatches;
}

static int devmem_write32(unsigned long addr, uint32_t value) {
  long page_size = sysconf(_SC_PAGESIZE);
  if (page_size <= 0)
    page_size = 4096;

  unsigned long page_base = addr & ~((unsigned long)page_size - 1);
  unsigned long page_off = addr - page_base;

  int fd = open("/dev/mem", O_RDWR | O_SYNC);
  if (fd < 0) {
    perror("/dev/mem");
    return -1;
  }

  void *map = mmap(NULL, page_size, PROT_READ | PROT_WRITE, MAP_SHARED, fd,
                   (off_t)page_base);
  if (map == MAP_FAILED) {
    perror("mmap /dev/mem");
    close(fd);
    return -1;
  }

  volatile uint32_t *reg = (volatile uint32_t *)((char *)map + page_off);
  uint32_t old_value = *reg;
  *reg = value;
  uint32_t new_value = *reg;
  printf("devmem 0x%08lx: 0x%08x -> 0x%08x\n", addr, old_value, new_value);

  munmap(map, page_size);
  close(fd);
  return 0;
}

static int devmem_read32(unsigned long addr, uint32_t *value) {
  long page_size = sysconf(_SC_PAGESIZE);
  if (page_size <= 0)
    page_size = 4096;

  unsigned long page_base = addr & ~((unsigned long)page_size - 1);
  unsigned long page_off = addr - page_base;

  int fd = open("/dev/mem", O_RDONLY | O_SYNC);
  if (fd < 0) {
    perror("/dev/mem");
    return -1;
  }

  void *map = mmap(NULL, page_size, PROT_READ, MAP_SHARED, fd,
                   (off_t)page_base);
  if (map == MAP_FAILED) {
    perror("mmap /dev/mem");
    close(fd);
    return -1;
  }

  volatile uint32_t *reg = (volatile uint32_t *)((char *)map + page_off);
  *value = *reg;

  munmap(map, page_size);
  close(fd);
  return 0;
}

static int write_devmem_entries(const struct devmem_entry *entries,
                                size_t count) {
  if (!count)
    return 0;

  printf("writing %zu devmem overrides\n", count);
  for (size_t i = 0; i < count; i++) {
    if (devmem_write32(entries[i].addr, entries[i].value) < 0)
      return -1;
  }

  return 0;
}

static int read_devmem_entries(const char *label,
                               const struct devmem_read_entry *entries,
                               size_t count) {
  if (!count)
    return 0;

  printf("%s\n", label);
  for (size_t i = 0; i < count; i++) {
    uint32_t value = 0;
    if (devmem_read32(entries[i].addr, &value) < 0)
      return -1;
    printf("  0x%08lx=0x%08x\n", entries[i].addr, value);
  }

  return 0;
}

static int setup_link_flags(int media_fd, unsigned int src_ent,
                            unsigned int src_pad, unsigned int sink_ent,
                            unsigned int sink_pad, unsigned int flags) {
  struct media_link_desc link = {};
  link.source.entity = src_ent;
  link.source.index = src_pad;
  link.sink.entity = sink_ent;
  link.sink.index = sink_pad;
  link.flags = flags;
  return xioctl(media_fd, MEDIA_IOC_SETUP_LINK, &link, "MEDIA_IOC_SETUP_LINK");
}

static int setup_link(int media_fd, unsigned int src_ent, unsigned int src_pad,
                      unsigned int sink_ent, unsigned int sink_pad) {
  return setup_link_flags(media_fd, src_ent, src_pad, sink_ent, sink_pad,
                          MEDIA_LNK_FL_ENABLED);
}

static void clear_known_sensor_links(int media_fd, const char *csid_name,
                                     unsigned int selected_csiphy,
                                     unsigned int csid_ent) {
  const char *csiphy_names[] = {
    "msm_csiphy0",
    "msm_csiphy2",
    "msm_csiphy3",
  };

  for (unsigned int i = 0; i < ARRAY_SIZE(csiphy_names); i++) {
    if (!strcmp(csid_name, "msm_csid1") &&
        strcmp(csiphy_names[i], "msm_csiphy3"))
      continue;
    if (!strcmp(csid_name, "msm_csid0") &&
        strcmp(csiphy_names[i], "msm_csiphy0") &&
        strcmp(csiphy_names[i], "msm_csiphy2"))
      continue;

    unsigned int csiphy = find_media_entity_by_name(media_fd,
                                                    csiphy_names[i], 0);
    if (!csiphy || csiphy == selected_csiphy)
      continue;

    setup_link_flags(media_fd, csiphy, 1, csid_ent, 0, 0);
  }
}

static int set_subdev_format(const char *path, unsigned int pad,
                             unsigned int code, unsigned int width,
                             unsigned int height) {
  int fd = open(path, O_RDWR);
  if (fd < 0) {
    perror(path);
    return -1;
  }

  struct v4l2_subdev_format fmt = {};
  fmt.which = V4L2_SUBDEV_FORMAT_ACTIVE;
  fmt.pad = pad;
  fmt.format.width = width;
  fmt.format.height = height;
  fmt.format.code = code;
  fmt.format.field = V4L2_FIELD_NONE;
  fmt.format.colorspace = V4L2_COLORSPACE_SRGB;

  int ret = xioctl(fd, VIDIOC_SUBDEV_S_FMT, &fmt, "VIDIOC_SUBDEV_S_FMT");
  if (ret == 0)
    printf("%s pad %u format %ux%u code=0x%x\n", path, pad,
           fmt.format.width, fmt.format.height, fmt.format.code);
  close(fd);
  return ret;
}

static int set_control(const char *path, unsigned int id, int value,
                       const char *label) {
  int fd = open(path, O_RDWR);
  if (fd < 0) {
    perror(path);
    return -1;
  }

  struct v4l2_control ctrl = {
    .id = id,
    .value = value,
  };
  int ret = xioctl(fd, VIDIOC_S_CTRL, &ctrl, label);
  if (ret == 0)
    printf("%s set to %d on %s\n", label, value, path);
  close(fd);
  return ret;
}

int main(int argc, char **argv) {
  const char *video_path = "/dev/video3";
  const char *out_path = "/tmp/cam3-rdi.raw";
  const char *sensor_subdev = "/dev/v4l-subdev27";
  const char *csiphy_subdev = "/dev/v4l-subdev3";
  const char *csid_subdev = "/dev/v4l-subdev6";
  const char *vfe_subdev = "/dev/v4l-subdev13";
  const char *sensor_name = "os04c10 20-0036";
  const char *csiphy_name = "msm_csiphy3";
  const char *csid_name = "msm_csid1";
  const char *vfe_name = "msm_vfe1_rdi0";
  const char *video_name = "msm_vfe1_video0";
  char resolved_sensor_subdev[64];
  char resolved_csiphy_subdev[64];
  char resolved_csid_subdev[64];
  char resolved_vfe_subdev[64];
  char resolved_video_path[64];
  unsigned int csiphy_ent = 10;
  unsigned int csid_ent = 22;
  unsigned int vfe_ent = 73;
  const char *init_reg_file = NULL;
  unsigned int width = 2688;
  unsigned int height = 1520;
  unsigned int pixfmt = fourcc('p', 'B', 'A', 'A'); // V4L2_PIX_FMT_SBGGR10P
  int frame_target = 3;
  int poll_iters = 50;
  int poll_timeout_ms = 100;
  int init_reg_limit = -1;
  int csid_testgen = 0;
  int devmem_hold_ms = 0;
  struct reg_entry pre_overrides[32] = {};
  size_t pre_override_count = 0;
  struct devmem_entry devmem_overrides[32] = {};
  size_t devmem_override_count = 0;
  struct devmem_read_entry devmem_reads[64] = {};
  size_t devmem_read_count = 0;
  bool devmem_only = false;
  bool no_start = false;
  bool fail_on_init_mismatch = false;
  bool skip_stream_readback = false;

  for (int i = 1; i < argc; i++) {
    if (!strcmp(argv[i], "--cam1")) {
      video_path = "/dev/video0";
      out_path = "/tmp/cam1-rdi.raw";
      csiphy_subdev = "/dev/v4l-subdev0";
      csid_subdev = "/dev/v4l-subdev4";
      vfe_subdev = "/dev/v4l-subdev10";
      sensor_name = "os04c10 16-0036";
      csiphy_name = "msm_csiphy0";
      csid_name = "msm_csid0";
      vfe_name = "msm_vfe0_rdi0";
      video_name = "msm_vfe0_video0";
      csiphy_ent = 1;
      csid_ent = 13;
      vfe_ent = 46;
      i2c_bus = 16;
      i2c_dev = "16-0036";
    } else if (!strcmp(argv[i], "--cam2")) {
      video_path = "/dev/video0";
      out_path = "/tmp/cam2-rdi.raw";
      csiphy_subdev = "/dev/v4l-subdev2";
      csid_subdev = "/dev/v4l-subdev5";
      vfe_subdev = "/dev/v4l-subdev10";
      sensor_name = "os04c10 18-0036";
      csiphy_name = "msm_csiphy2";
      csid_name = "msm_csid0";
      vfe_name = "msm_vfe0_rdi0";
      video_name = "msm_vfe0_video0";
      csiphy_ent = 7;
      csid_ent = 16;
      vfe_ent = 46;
      i2c_bus = 18;
      i2c_dev = "18-0036";
    } else if (!strcmp(argv[i], "--cam3")) {
      video_path = "/dev/video3";
      out_path = "/tmp/cam3-rdi.raw";
      csiphy_subdev = "/dev/v4l-subdev3";
      csid_subdev = "/dev/v4l-subdev6";
      vfe_subdev = "/dev/v4l-subdev13";
      sensor_name = "os04c10 20-0036";
      csiphy_name = "msm_csiphy3";
      csid_name = "msm_csid1";
      vfe_name = "msm_vfe1_rdi0";
      video_name = "msm_vfe1_video0";
      csiphy_ent = 10;
      csid_ent = 22;
      vfe_ent = 73;
      i2c_bus = 20;
      i2c_dev = "20-0036";
    } else if (!strcmp(argv[i], "--raw12"))
      pixfmt = fourcc('p', 'B', 'C', 'C'); // V4L2_PIX_FMT_SBGGR12P
    else if (!strcmp(argv[i], "--raw10"))
      pixfmt = fourcc('p', 'B', 'A', 'A');
    else if (!strcmp(argv[i], "--init-reg-file") && i + 1 < argc)
      init_reg_file = argv[++i];
    else if (!strcmp(argv[i], "--init-reg-limit") && i + 1 < argc)
      init_reg_limit = atoi(argv[++i]);
    else if (!strcmp(argv[i], "--csid-testgen") && i + 1 < argc)
      csid_testgen = atoi(argv[++i]);
    else if (!strcmp(argv[i], "--pre-override") && i + 1 < argc) {
      if (pre_override_count == ARRAY_SIZE(pre_overrides)) {
        fprintf(stderr, "too many --pre-override values\n");
        return 1;
      }
      char arg[64];
      snprintf(arg, sizeof(arg), "%s", argv[++i]);
      struct reg_entry entry = {};
      if (parse_reg_line(arg, &entry) != 1 || entry.is_delay) {
        fprintf(stderr, "invalid --pre-override, expected addr=value\n");
        return 1;
      }
      pre_overrides[pre_override_count++] = entry;
    }
    else if (!strcmp(argv[i], "--devmem-write") && i + 1 < argc) {
      if (devmem_override_count == ARRAY_SIZE(devmem_overrides)) {
        fprintf(stderr, "too many --devmem-write values\n");
        return 1;
      }
      char arg[64];
      snprintf(arg, sizeof(arg), "%s", argv[++i]);
      if (parse_devmem_arg(arg, &devmem_overrides[devmem_override_count]) < 0) {
        fprintf(stderr, "invalid --devmem-write, expected addr=value\n");
        return 1;
      }
      devmem_override_count++;
    }
    else if (!strcmp(argv[i], "--devmem-read") && i + 1 < argc) {
      if (devmem_read_count == ARRAY_SIZE(devmem_reads)) {
        fprintf(stderr, "too many --devmem-read values\n");
        return 1;
      }
      devmem_reads[devmem_read_count++].addr = strtoul(argv[++i], NULL, 0);
    }
    else if (!strcmp(argv[i], "--devmem-only"))
      devmem_only = true;
    else if (!strcmp(argv[i], "--devmem-hold-ms") && i + 1 < argc)
      devmem_hold_ms = atoi(argv[++i]);
    else if (!strcmp(argv[i], "--i2c-bus") && i + 1 < argc)
      i2c_bus = atoi(argv[++i]);
    else if (!strcmp(argv[i], "--i2c-addr") && i + 1 < argc)
      i2c_addr = (int)strtoul(argv[++i], NULL, 0);
    else if (!strcmp(argv[i], "--no-start"))
      no_start = true;
    else if (!strcmp(argv[i], "--skip-stream-readback"))
      skip_stream_readback = true;
    else if (!strcmp(argv[i], "--fail-on-init-mismatch"))
      fail_on_init_mismatch = true;
    else if (!strcmp(argv[i], "--frames") && i + 1 < argc)
      frame_target = atoi(argv[++i]);
    else if (!strcmp(argv[i], "--width") && i + 1 < argc)
      width = strtoul(argv[++i], NULL, 0);
    else if (!strcmp(argv[i], "--height") && i + 1 < argc)
      height = strtoul(argv[++i], NULL, 0);
    else if (!strcmp(argv[i], "--out") && i + 1 < argc)
      out_path = argv[++i];
    else if (!strcmp(argv[i], "--polls") && i + 1 < argc)
      poll_iters = atoi(argv[++i]);
    else if (!strcmp(argv[i], "--poll-ms") && i + 1 < argc)
      poll_timeout_ms = atoi(argv[++i]);
    else {
      fprintf(stderr,
              "usage: %s [--raw10|--raw12] [--init-reg-file PATH] "
              "[--cam1|--cam2|--cam3] "
              "[--init-reg-limit N] [--i2c-bus N] [--i2c-addr 0x36] "
              "[--csid-testgen N] "
              "[--pre-override addr=value] "
              "[--devmem-write addr=value] [--devmem-read addr] "
              "[--devmem-only] [--devmem-hold-ms N] "
              "[--no-start] [--skip-stream-readback] "
              "[--fail-on-init-mismatch] [--frames N] "
              "[--width W] [--height H] "
              "[--out PATH] [--polls N] [--poll-ms N]\n",
              argv[0]);
      return 1;
    }
  }

  if (devmem_only) {
    if (write_devmem_entries(devmem_overrides, devmem_override_count) < 0)
      return 1;
    if (read_devmem_entries("devmem_read",
                            devmem_reads, devmem_read_count) < 0)
      return 1;
    if (devmem_hold_ms > 0) {
      printf("holding after devmem writes for %d ms\n", devmem_hold_ms);
      usleep((useconds_t)devmem_hold_ms * 1000);
    }
    return 0;
  }

  if (find_v4l_dev_by_name("v4l-subdev", sensor_name,
                           resolved_sensor_subdev,
                           sizeof(resolved_sensor_subdev)) == 0)
    sensor_subdev = resolved_sensor_subdev;
  if (find_v4l_dev_by_name("v4l-subdev", csiphy_name,
                           resolved_csiphy_subdev,
                           sizeof(resolved_csiphy_subdev)) == 0)
    csiphy_subdev = resolved_csiphy_subdev;
  if (find_v4l_dev_by_name("v4l-subdev", csid_name,
                           resolved_csid_subdev,
                           sizeof(resolved_csid_subdev)) == 0)
    csid_subdev = resolved_csid_subdev;
  if (find_v4l_dev_by_name("v4l-subdev", vfe_name,
                           resolved_vfe_subdev,
                           sizeof(resolved_vfe_subdev)) == 0)
    vfe_subdev = resolved_vfe_subdev;
  if (find_v4l_dev_by_name("video", video_name,
                           resolved_video_path,
                           sizeof(resolved_video_path)) == 0)
    video_path = resolved_video_path;

  int sensor_power_fd = -1;
  if (!csid_testgen) {
    force_sensor_runtime_power_on(i2c_dev);
    sensor_power_fd = open(sensor_subdev, O_RDWR);
    if (sensor_power_fd >= 0)
      printf("holding sensor subdev open: %s\n", sensor_subdev);
    else
      fprintf(stderr, "warning: %s: %s\n", sensor_subdev, strerror(errno));
    if (wake_sensor_subdev(sensor_power_fd) < 0)
      fprintf(stderr, "warning: sensor subdev wake write failed\n");
  } else {
    printf("CSID test generator mode %d: skipping sensor init/start\n",
           csid_testgen);
  }

  struct reg_entry *init_regs = NULL;
  size_t init_reg_count = 0;
  if (init_reg_file && !csid_testgen) {
    if (load_reg_file(init_reg_file, &init_regs, &init_reg_count, init_reg_limit) < 0)
      return 1;
    if (write_reg_entries(init_regs, init_reg_count) < 0)
      return 1;
    usleep(100000);
    int init_mismatches = sensor_readback("sensor_readback_after_init");
    if (init_mismatches && fail_on_init_mismatch) {
      free(init_regs);
      return 1;
    }
  }

  int media_fd = open("/dev/media0", O_RDWR);
  if (media_fd < 0) {
    perror("/dev/media0");
    return 1;
  }

  csiphy_ent = find_media_entity_by_name(media_fd, csiphy_name, csiphy_ent);
  csid_ent = find_media_entity_by_name(media_fd, csid_name, csid_ent);
  vfe_ent = find_media_entity_by_name(media_fd, vfe_name, vfe_ent);

  printf("route entities: sensor=%s %s csiphy=%u %s csid=%u %s vfe_rdi=%u %s video=%s\n",
         sensor_name, sensor_subdev, csiphy_ent, csiphy_subdev,
         csid_ent, csid_subdev, vfe_ent, vfe_subdev, video_path);
  clear_known_sensor_links(media_fd, csid_name, csiphy_ent, csid_ent);
  if (csid_testgen) {
    if (setup_link_flags(media_fd, csiphy_ent, 1, csid_ent, 0, 0) < 0)
      return 1;
    if (set_control(csid_subdev, V4L2_CID_TEST_PATTERN, csid_testgen,
                    "V4L2_CID_TEST_PATTERN") < 0)
      return 1;
  } else {
    if (setup_link(media_fd, csiphy_ent, 1, csid_ent, 0) < 0)
      return 1;
  }
  if (setup_link(media_fd, csid_ent, 1, vfe_ent, 0) < 0)
    return 1;
  close(media_fd);

  unsigned int mbus_code = pixfmt == fourcc('p', 'B', 'C', 'C') ?
                           MEDIA_BUS_FMT_SBGGR12_1X12 :
                           MEDIA_BUS_FMT_SBGGR10_1X10;
  if (!csid_testgen) {
    if (set_subdev_format(sensor_subdev, 0, mbus_code, width, height) < 0)
      return 1;
    if (set_subdev_format(csiphy_subdev, 0, mbus_code, width, height) < 0)
      return 1;
    if (set_subdev_format(csiphy_subdev, 1, mbus_code, width, height) < 0)
      return 1;
    if (set_subdev_format(csid_subdev, 0, mbus_code, width, height) < 0)
      return 1;
  }
  if (set_subdev_format(csid_subdev, 1, mbus_code, width, height) < 0)
    return 1;
  if (set_subdev_format(vfe_subdev, 0, mbus_code, width, height) < 0)
    return 1;
  if (set_subdev_format(vfe_subdev, 1, mbus_code, width, height) < 0)
    return 1;

  int vfd = open(video_path, O_RDWR | O_NONBLOCK);
  if (vfd < 0) {
    perror(video_path);
    return 1;
  }

  struct v4l2_capability cap = {};
  if (xioctl(vfd, VIDIOC_QUERYCAP, &cap, "VIDIOC_QUERYCAP") == 0)
    printf("video driver=%s card=%s caps=0x%x device_caps=0x%x\n",
           cap.driver, cap.card, cap.capabilities, cap.device_caps);

  struct v4l2_format fmt = {};
  fmt.type = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE;
  fmt.fmt.pix_mp.width = width;
  fmt.fmt.pix_mp.height = height;
  fmt.fmt.pix_mp.pixelformat = pixfmt;
  fmt.fmt.pix_mp.num_planes = 1;
  if (xioctl(vfd, VIDIOC_S_FMT, &fmt, "VIDIOC_S_FMT") < 0)
    return 1;
  printf("format %ux%u fourcc=", fmt.fmt.pix_mp.width, fmt.fmt.pix_mp.height);
  print_fourcc(fmt.fmt.pix_mp.pixelformat);
  printf(" planes=%u bytesperline=%u sizeimage=%u\n",
         fmt.fmt.pix_mp.num_planes,
         fmt.fmt.pix_mp.plane_fmt[0].bytesperline,
         fmt.fmt.pix_mp.plane_fmt[0].sizeimage);

  struct v4l2_requestbuffers req = {};
  req.count = 4;
  req.type = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE;
  req.memory = V4L2_MEMORY_MMAP;
  if (xioctl(vfd, VIDIOC_REQBUFS, &req, "VIDIOC_REQBUFS") < 0)
    return 1;
  if (req.count < 2) {
    fprintf(stderr, "not enough buffers: %u\n", req.count);
    return 1;
  }

  struct buffer bufs[8] = {};
  if (req.count > ARRAY_SIZE(bufs))
    req.count = ARRAY_SIZE(bufs);

  for (unsigned int i = 0; i < req.count; i++) {
    struct v4l2_plane planes[VIDEO_MAX_PLANES] = {};
    struct v4l2_buffer buf = {};
    buf.type = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE;
    buf.memory = V4L2_MEMORY_MMAP;
    buf.index = i;
    buf.length = ARRAY_SIZE(planes);
    buf.m.planes = planes;
    if (xioctl(vfd, VIDIOC_QUERYBUF, &buf, "VIDIOC_QUERYBUF") < 0)
      return 1;
    bufs[i].length = planes[0].length;
    bufs[i].start = mmap(NULL, bufs[i].length, PROT_READ | PROT_WRITE,
                         MAP_SHARED, vfd, planes[0].m.mem_offset);
    if (bufs[i].start == MAP_FAILED) {
      perror("mmap");
      return 1;
    }
    if (xioctl(vfd, VIDIOC_QBUF, &buf, "VIDIOC_QBUF") < 0)
      return 1;
  }

  int type = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE;
  if (!csid_testgen) {
    printf("sensor_stop rc=%d\n", i2c_write_reg(0x0100, 0x00));
    expected_set[0x0100] = true;
    expected_value[0x0100] = 0x00;
    if (pre_override_count) {
      printf("writing %zu pre-stream overrides\n", pre_override_count);
      for (size_t i = 0; i < pre_override_count; i++) {
        printf("  0x%04x=0x%02x\n", pre_overrides[i].reg,
               pre_overrides[i].value);
        if (i2c_write_reg(pre_overrides[i].reg, pre_overrides[i].value) != 0) {
          fprintf(stderr, "pre-stream override failed: 0x%04x=0x%02x\n",
                  pre_overrides[i].reg, pre_overrides[i].value);
          return 1;
        }
        expected_set[pre_overrides[i].reg] = true;
        expected_value[pre_overrides[i].reg] = pre_overrides[i].value;
      }
    }
    sensor_readback("sensor_readback_before_video_streamon");
  }
  if (xioctl(vfd, VIDIOC_STREAMON, &type, "VIDIOC_STREAMON") < 0)
    return 1;
  if (write_devmem_entries(devmem_overrides, devmem_override_count) < 0)
    return 1;
  if (read_devmem_entries("devmem_read_after_video_streamon",
                          devmem_reads, devmem_read_count) < 0)
    return 1;

  if (csid_testgen) {
    printf("video streamon ok, CSID testgen active; polling without sensor start\n");
  } else if (no_start) {
    printf("video streamon ok, --no-start requested; polling without sensor start\n");
  } else {
    printf("video streamon ok, starting sensor\n");
    expected_value[0x0100] = 0x01;
    printf("sensor_start rc=%d\n", i2c_write_reg(0x0100, 0x01));
    usleep(200000);
    if (read_devmem_entries("devmem_read_after_sensor_start",
                            devmem_reads, devmem_read_count) < 0)
      return 1;
    if (!skip_stream_readback)
      sensor_readback("sensor_readback_after_start");
  }

  FILE *out = fopen(out_path, "wb");
  int frames = 0;
  for (int iter = 0; iter < poll_iters && frames < frame_target; iter++) {
    struct pollfd pfd = {.fd = vfd, .events = POLLIN | POLLERR};
    int pret = poll(&pfd, 1, poll_timeout_ms);
    if (pret < 0) {
      perror("poll");
      break;
    }
    if (pret == 0)
      continue;

    struct v4l2_plane planes[VIDEO_MAX_PLANES] = {};
    struct v4l2_buffer buf = {};
    buf.type = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE;
    buf.memory = V4L2_MEMORY_MMAP;
    buf.length = ARRAY_SIZE(planes);
    buf.m.planes = planes;
    if (xioctl(vfd, VIDIOC_DQBUF, &buf, "VIDIOC_DQBUF") < 0)
      break;

    printf("frame %d index=%u bytesused=%u seq=%u ts=%ld.%06ld\n",
           frames, buf.index, planes[0].bytesused, buf.sequence,
           buf.timestamp.tv_sec, buf.timestamp.tv_usec);
    if (out && frames == 0)
      fwrite(bufs[buf.index].start, 1, planes[0].bytesused, out);
    frames++;
    if (xioctl(vfd, VIDIOC_QBUF, &buf, "VIDIOC_QBUF") < 0)
      break;
  }
  if (out)
    fclose(out);

  if (read_devmem_entries("devmem_read_after_poll",
                          devmem_reads, devmem_read_count) < 0)
    return 1;
  printf("frames=%d output=%s\n", frames, frames ? out_path : "none");
  if (!skip_stream_readback && !csid_testgen)
    sensor_readback("sensor_readback_before_stop");
  if (!csid_testgen)
    i2c_write_reg(0x0100, 0x00);
  xioctl(vfd, VIDIOC_STREAMOFF, &type, "VIDIOC_STREAMOFF");
  if (csid_testgen)
    set_control(csid_subdev, V4L2_CID_TEST_PATTERN, 0,
                "V4L2_CID_TEST_PATTERN");
  for (unsigned int i = 0; i < req.count; i++)
    if (bufs[i].start && bufs[i].start != MAP_FAILED)
      munmap(bufs[i].start, bufs[i].length);
  close(vfd);
  if (sensor_power_fd >= 0)
    close(sensor_power_fd);
  free(init_regs);
  return frames ? 0 : 2;
}
