// SPDX-License-Identifier: MIT

#include <errno.h>
#include <linux/reboot.h>
#include <stdio.h>
#include <string.h>
#include <sys/syscall.h>
#include <unistd.h>

static void usage(FILE *stream, const char *program)
{
	fprintf(stream, "Usage: sudo %s\n", program);
}

int main(int argc, char **argv)
{
	if (argc == 2 && (!strcmp(argv[1], "-h") || !strcmp(argv[1], "--help"))) {
		usage(stdout, argv[0]);
		return 0;
	}

	if (argc != 1) {
		usage(stderr, argv[0]);
		return 2;
	}

	if (geteuid() != 0) {
		fprintf(stderr, "%s: root privileges are required\n", argv[0]);
		return 1;
	}

	/* Persist buffered writes before handing control to Qualcomm firmware. */
	sync();

	if (syscall(SYS_reboot, LINUX_REBOOT_MAGIC1, LINUX_REBOOT_MAGIC2,
		    LINUX_REBOOT_CMD_RESTART2, "edl") == -1) {
		fprintf(stderr, "%s: failed to reboot into EDL: %s\n", argv[0],
			strerror(errno));
		return 1;
	}

	return 0;
}
