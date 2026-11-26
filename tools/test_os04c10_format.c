/* Check a fitted OS04C10 without changing its active format or streaming state. */
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <time.h>
#include <linux/media-bus-format.h>
#include <linux/v4l2-subdev.h>
#include <stdio.h>
#include <sys/ioctl.h>
#include <unistd.h>

int main(int argc, char **argv)
{
	if (argc != 2) {
		fprintf(stderr, "usage: %s /dev/v4l-subdevN\n", argv[0]);
		return 2;
	}
	int fd = open(argv[1], O_RDWR);
	if (fd < 0) {
		perror(argv[1]);
		return 1;
	}
	struct v4l2_subdev_mbus_code_enum code = {
		.which = V4L2_SUBDEV_FORMAT_TRY,
	};
	assert(ioctl(fd, VIDIOC_SUBDEV_ENUM_MBUS_CODE, &code) == 0);
	assert(code.code == MEDIA_BUS_FMT_SBGGR12_1X12);
	code.index = 1;
	assert(ioctl(fd, VIDIOC_SUBDEV_ENUM_MBUS_CODE, &code) == -1 && errno == EINVAL);

	struct v4l2_subdev_format fmt = { .which = V4L2_SUBDEV_FORMAT_TRY };
	assert(ioctl(fd, VIDIOC_SUBDEV_G_FMT, &fmt) == 0);
	assert(fmt.format.code == MEDIA_BUS_FMT_SBGGR12_1X12);
	assert(fmt.format.width == 2688 && fmt.format.height == 1520);
	const unsigned int requests[] = { MEDIA_BUS_FMT_SBGGR10_1X10, 0, MEDIA_BUS_FMT_SBGGR12_1X12 };
	for (unsigned int i = 0; i < sizeof(requests) / sizeof(requests[0]); i++) {
		fmt.format.code = requests[i];
		fmt.format.width = i ? 1344 : 0;
		fmt.format.height = i ? 760 : 2000;
		assert(ioctl(fd, VIDIOC_SUBDEV_S_FMT, &fmt) == 0);
		assert(fmt.format.code == MEDIA_BUS_FMT_SBGGR12_1X12);
		assert(fmt.format.width == (i ? 1344 : 2688));
		assert(fmt.format.height == (i ? 760 : 1520));
		assert(ioctl(fd, VIDIOC_SUBDEV_G_FMT, &fmt) == 0);
		assert(fmt.format.code == MEDIA_BUS_FMT_SBGGR12_1X12);
	}
	struct v4l2_subdev_frame_size_enum size = {
		.which = V4L2_SUBDEV_FORMAT_TRY,
		.code = MEDIA_BUS_FMT_SBGGR10_1X10,
	};
	assert(ioctl(fd, VIDIOC_SUBDEV_ENUM_FRAME_SIZE, &size) == -1 && errno == EINVAL);
	size.code = MEDIA_BUS_FMT_SBGGR12_1X12;
	assert(ioctl(fd, VIDIOC_SUBDEV_ENUM_FRAME_SIZE, &size) == 0);
	assert(size.min_width == 1 && size.max_width == 2688);
	assert(size.min_height == 1 && size.max_height == 1520);
	close(fd);
	printf("%s: RAW12-only enumeration, defaults, negotiation and frame sizes passed\n", argv[1]);
	return 0;
}
