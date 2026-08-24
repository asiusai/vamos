// SPDX-License-Identifier: MIT

#include <errno.h>
#include <stdio.h>
#include <string.h>
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

	/* Persist application writes before arming the redundant boot metadata. */
	sync();

	/*
	 * Linux runs at EL1 on the Dragon, where the stock secure monitor ignores
	 * the EDL cookie during reset.  Persist a one-shot request and let the
	 * proven U-Boot EL2 recovery command consume it on the next boot.
	 */
	execl("/usr/bin/vamos-update", "vamos-update", "request-edl", NULL);

	fprintf(stderr, "%s: failed to execute vamos-update request-edl: %s\n",
		argv[0], strerror(errno));
	return 1;
}
