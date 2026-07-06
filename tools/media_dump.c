// SPDX-License-Identifier: MIT
// Minimal /dev/media0 graph dump for Dragon camera bring-up.

#include <errno.h>
#include <fcntl.h>
#include <linux/media.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>

static const char *pad_flags(unsigned int flags) {
  if (flags & MEDIA_PAD_FL_SOURCE) return "source";
  if (flags & MEDIA_PAD_FL_SINK) return "sink";
  return "-";
}

int main(int argc, char **argv) {
  const char *dev = argc > 1 ? argv[1] : "/dev/media0";
  int fd = open(dev, O_RDWR);
  if (fd < 0) {
    perror(dev);
    return 1;
  }

  struct media_device_info info = {};
  if (ioctl(fd, MEDIA_IOC_DEVICE_INFO, &info) == 0) {
    printf("driver=%s model=%s bus=%s media=0x%x\n",
           info.driver, info.model, info.bus_info, info.media_version);
  }

  for (unsigned int next = MEDIA_ENT_ID_FLAG_NEXT;;) {
    struct media_entity_desc ent = {};
    ent.id = next;
    if (ioctl(fd, MEDIA_IOC_ENUM_ENTITIES, &ent) < 0) {
      if (errno == EINVAL)
        break;
      perror("MEDIA_IOC_ENUM_ENTITIES");
      close(fd);
      return 1;
    }

    printf("entity id=%u name=\"%s\" type=0x%x pads=%u links=%u dev=%u:%u\n",
           ent.id, ent.name, ent.type, ent.pads, ent.links,
           ent.dev.major, ent.dev.minor);

    if (ent.pads || ent.links) {
      struct media_pad_desc pads[64] = {};
      struct media_link_desc links[128] = {};
      struct media_links_enum le = {};
      le.entity = ent.id;
      le.pads = pads;
      le.links = links;
      if (ent.pads > 64 || ent.links > 128) {
        fprintf(stderr, "entity %u has too many pads/links for this helper\n", ent.id);
      } else if (ioctl(fd, MEDIA_IOC_ENUM_LINKS, &le) == 0) {
        for (unsigned int i = 0; i < ent.pads; i++) {
          printf("  pad %u %s flags=0x%x\n",
                 pads[i].index, pad_flags(pads[i].flags), pads[i].flags);
        }
        for (unsigned int i = 0; i < ent.links; i++) {
          const char *enabled = links[i].flags & MEDIA_LNK_FL_ENABLED ? "enabled" : "disabled";
          printf("  link %u:%u -> %u:%u flags=0x%x %s\n",
                 links[i].source.entity, links[i].source.index,
                 links[i].sink.entity, links[i].sink.index,
                 links[i].flags, enabled);
        }
      } else {
        perror("MEDIA_IOC_ENUM_LINKS");
      }
    }

    next = ent.id | MEDIA_ENT_ID_FLAG_NEXT;
  }

  close(fd);
  return 0;
}
