# vamOS Void Linux Build
# check=error=true

ARG OPENPILOT_PYTHON_VERSION=3.12.13

# ################## #
# ###### Base ###### #
# ################## #
FROM scratch AS void-base

ARG OPENPILOT_PYTHON_VERSION
ENV OPENPILOT_PYTHON_VERSION=${OPENPILOT_PYTHON_VERSION}

# Add Void Linux aarch64 rootfs
ARG VOID_ROOTFS
ADD ${VOID_ROOTFS} /

# Build folder
RUN mkdir -p /tmp/vamos

ARG USERNAME=comma

# Base system setup
COPY ./userspace/base_setup.sh /tmp/vamos/
RUN /tmp/vamos/base_setup.sh

# Qualcomm QCS6490 FastRPC and QAIRT v68 runtime. The installer fetches pinned,
# checksum-verified Radxa packages and installs only the cDSP runtime pieces.
COPY ./userspace/audio/QCS6490-Asius-Panda-tplg.bin /tmp/vamos/audio/
COPY ./userspace/install_qnn_runtime.sh /tmp/vamos/
RUN /tmp/vamos/install_qnn_runtime.sh && rm -rf /tmp/vamos/install_qnn_runtime.sh /tmp/vamos/audio

# ################### #
# ###### vamOS ###### #
# ################### #
FROM void-base

ARG USERNAME=comma

# Native low-overhead /var/log safety monitor. This replaces AGNOS's resident
# Python poller while preserving the same last-line-of-defense behavior.
COPY ./userspace/varwatch/varwatch.c /tmp/vamos/varwatch.c
RUN gcc -Wall -Wextra -Werror -O2 -o /usr/bin/vamos-varwatch /tmp/vamos/varwatch.c && \
    rm /tmp/vamos/varwatch.c

# Persist a redundant one-shot request for the proven U-Boot EDL command.
COPY ./userspace/reboot-edl/reboot-edl.c /tmp/vamos/reboot-edl.c
RUN gcc -Wall -Wextra -Werror -O2 -o /usr/bin/reboot-edl /tmp/vamos/reboot-edl.c && \
    rm /tmp/vamos/reboot-edl.c

# Mesa DRI drivers (freedreno for Qualcomm Adreno GPU)
# Required for EGL/GBM to work with the display
RUN yes | xbps-install -Sy mesa-dri

# Asius Connect uses Tailscale for remote access.
RUN xbps-install -y tailscale

# Python packages via uv
ARG XDG_DATA_HOME="/usr/local"
ENV PATH="/root/.local/bin:$XDG_DATA_HOME/venv/bin:$PATH"
COPY ./userspace/uv /tmp/vamos/uv
RUN cd /tmp/vamos/uv && \
    MAKEFLAGS="-j$(nproc)" UV_CONCURRENT_BUILDS=4 UV_NO_CACHE=1 UV_PROJECT_ENVIRONMENT=$XDG_DATA_HOME/venv \
    uv sync --frozen --inexact

# Install AIC8800 USB WiFi/BT firmware.
COPY ./kernel/firmware/aic8800_fw/USB /lib/firmware/aic8800_fw/USB
RUN ln -sf aic8800_fw/USB/aic8800D80 /lib/firmware/aic8800D80

# Qualcomm GENI firmware for I2C/SPI/UART serial engines.
COPY ./kernel/firmware/qcom/qcm6490/qupv3fw.elf /lib/firmware/qcom/qcm6490/qupv3fw.elf

# Qualcomm Venus video encoder/decoder firmware from linux-firmware.
COPY ./kernel/firmware/qcom/vpu-2.0/venus.mbn /lib/firmware/qcom/vpu-2.0/venus.mbn
COPY ./kernel/firmware/LICENSES/LICENSE.qcom ./kernel/firmware/LICENSES/NOTICE.qcom /usr/share/licenses/linux-firmware/

# Kernel modules are installed after Docker export in build_system.sh
# (in-tree from build/modules_install + out-of-tree prebuilt from kernel/modules/)

# Root filesystem overlay — all userspace files at their final paths
COPY ./userspace/root/ /

# Screenless setup/reset implementations. The source overlay retains the AGNOS
# zipapps for reference, but they cannot be operated on v1 and waste 47 MiB.
COPY ./userspace/headless/setup /usr/comma/setup
COPY ./userspace/headless/reset /usr/comma/reset
RUN chmod 755 /usr/comma/setup /usr/comma/reset && \
    rm -f /usr/comma/installer /usr/comma/varwatch.py

# Remove SDM845 display/modem/Android compatibility payloads that no v1 runtime
# loads. QCS6490 TinyMesa (MSM) and the pinned FastRPC/QNN libraries remain.
RUN rm -rf \
      /etc/init.d /etc/initscripts \
      /etc/sv/adsp /etc/sv/cdsp /etc/sv/fs_setup /etc/sv/gpio \
      /etc/sv/ipa_fws /etc/sv/irsc_util /etc/sv/leprop /etc/sv/lte \
      /etc/sv/modemmanager /etc/sv/rmtfs \
      /usr/comma/lte /usr/comma/wlan /usr/local/qr-linux \
      /usr/lib/firmware/ipa /usr/lib/firmware/wlan/qca_cld \
      /usr/lib/NetworkManager/system-connections/lte.nmconnection \
      /system/rfs && \
    rm -f \
      /usr/bin/abctl /usr/bin/crasher64 /usr/bin/debuggerd64 \
      /usr/bin/getprop /usr/bin/leprop-service /usr/bin/logd /usr/bin/pdapitest \
      /usr/bin/qseecom_sample_client /usr/bin/qseecomd /usr/bin/setprop \
      /usr/lib/firmware/CAMERA_ICP.elf /usr/lib/firmware/ipa_fws.* \
      /usr/lib/libC2D2.so /usr/lib/libQTapGLES.so \
      /usr/lib/libc2d30-a5xx.so /usr/lib/libc2d30_bltlib.so \
      /usr/lib/libeglSubDriverWayland.so /usr/lib/libllvm-glnext.so \
      /usr/lib/libq3dtools_esx.so /usr/lib/libqmapboxgl.so \
      /usr/lib/libQseeComApi.so* \
      /usr/lib/libabctl.so* /usr/lib/libadbd.so* /usr/lib/libadsprpc.so \
      /usr/lib/libadsp_default_listener.so /usr/lib/libbacktrace.so* \
      /usr/lib/libbase.so* /usr/lib/libbinder.so* /usr/lib/libbtnv.so* \
      /usr/lib/libconfigdb.so* /usr/lib/libcutils.so* /usr/lib/libdiag.so* \
      /usr/lib/libdrmfs.so* /usr/lib/libdrmtime.so* /usr/lib/libdsutils.so* \
      /usr/lib/libext4_utils.so* /usr/lib/libfs_mgr.so* /usr/lib/libgpt.so* \
      /usr/lib/libhardware.so* /usr/lib/libhdcp1prov.so* /usr/lib/libhdcp2p2prov.so* \
      /usr/lib/liblmclient.so /usr/lib/liblog.so* /usr/lib/liblogwrap.so* \
      /usr/lib/libmdsprpc.so /usr/lib/libmincrypt.so* \
      /usr/lib/libpcre.so.1 /usr/lib/libpcre.so.1.2.6 \
      /usr/lib/libpdmapper.so* /usr/lib/libpdnotifier.so* /usr/lib/libplatformconfig.so* \
      /usr/lib/libqdMetaData.so /usr/lib/libqmi.so* /usr/lib/libqmi_cci.so* \
      /usr/lib/libqmi_client_qmux.so* /usr/lib/libqmi_common_so.so* \
      /usr/lib/libqmi_csi.so* /usr/lib/libqmi_encdec.so* /usr/lib/libqmi_sap.so* \
      /usr/lib/libqmiidl.so* /usr/lib/libqmiservices.so* /usr/lib/librpmb.so* \
      /usr/lib/libsdsprpc.so /usr/lib/libsdsp_default_listener.so \
      /usr/lib/libselinux.so.0 /usr/lib/libselinux.so.0.0.0 \
      /usr/lib/libsparse.so* /usr/lib/libssd.so* \
      /usr/lib/libsync.so* /usr/lib/libsysutils.so* /usr/lib/libtime_genoff.so* \
      /usr/lib/libunwind.so.0 /usr/lib/libunwind.so.0.0.0 \
      /usr/lib/libutils.so* /usr/lib/libxml.so*

# The overlay still carries legacy FastRPC development blobs. Keep the names
# for compatibility, but point them at the matched Radxa 1.0.6 runtime.
RUN ln -sfn libcdsprpc.so.1 /usr/lib/libcdsprpc.so && \
    ln -sfn libcdsp_default_listener.so.1 /usr/lib/libcdsp_default_listener.so && \
    ldconfig

COPY ./build/BOOTAA64.EFI /usr/share/vamos/BOOTAA64.EFI

# Fix ownership on home directory (overlay COPY runs as root)
RUN chown -R comma:comma /home/comma
# Void's default .bashrc doesn't source .bash_aliases
RUN printf '\nif [ -f ~/.bash_aliases ]; then\n    . ~/.bash_aliases\nfi\n' >> /home/$USERNAME/.bashrc
RUN mkdir -p /root/.config

# Enable the minimal Dragon services. Comma hardware services for its screen,
# modem, and SDM845 remote processors remain disabled.
RUN for svc in \
  sshd rsyslogd busybox-ntpd iptables \
  avahi-ssh-publish ssh-param-watcher ncm-param-watcher dnsmasq varwatch power_monitor \
  tailscaled \
  keygen \
  bluetoothd \
  github-runner \
  openpilot init-qcom sound vamos-boot-commit vamos-hypervisor-reboot \
  agetty-ttyMSM0 agetty-ttyAMA0; do \
    ln -sf /etc/sv/$svc /etc/runit/runsvdir/default/; \
  done

# Remove udevd service - we start udevd in runit stage 1, no need for service
RUN rm -f /etc/runit/runsvdir/default/udevd

# Make logs readable by comma user (for journalctl shim)
RUN sed -i 's/FileCreateMode 0640/FileCreateMode 0644/' /etc/rsyslog.conf

# Set executable permissions
RUN chmod +x /usr/comma/comma.sh /usr/comma/get-serial.sh /usr/comma/set_ssh.sh /usr/comma/set_ncm.sh /usr/bin/journalctl /usr/bin/systemctl /usr/bin/vamos-update /usr/bin/vamos-boot /usr/bin/vamos-hypervisor /usr/bin/vamos-wifi /usr/local/bin/qnn-smoke /etc/sv/keygen/run /etc/sv/bluetoothd/run /etc/sv/ncm-param-watcher/run /etc/sv/ncm-param-watcher/finish /etc/sv/dnsmasq/run /etc/sv/tailscaled/run /etc/sv/vamos-boot-commit/run /etc/sv/vamos-hypervisor-reboot/run
RUN chmod +x /etc/runit/1 /etc/runit/2 /etc/runit/3

# Shutdown wrappers - send wall broadcast like systemd does on Ubuntu
# Only poweroff and reboot: halt/shutdown must NOT be wrapped because Void's
# /usr/bin/shutdown script calls halt/reboot internally, causing cascading walls
RUN chmod +x /usr/local/sbin/poweroff && \
    ln -sf poweroff /usr/local/sbin/reboot

# NetworkManager connection permissions. V1 has no baked-in connection after
# removing comma's LTE profile; persistent Wi-Fi profiles live under /data.
RUN find /usr/lib/NetworkManager/system-connections -maxdepth 1 \
      -type f -name '*.nmconnection' -exec chmod 600 {} +

# MOTD - profile.d runs update-motd.d scripts into /run/motd, pam_motd displays it
RUN sed -i 's|motd=/etc/motd|motd=/run/motd|' /etc/pam.d/system-login

# Hourly logrotate cron job
RUN chmod 644 /etc/logrotate.conf /etc/logrotate.d/rsyslog && \
    chmod 755 /etc/cron.hourly/logrotate

# Mount point directories used by runtime partition mounts
RUN mkdir -p /dsp /firmware && \
    chown comma:comma /dsp /firmware

# /system/persist structure (copied to /data on first boot by fs_setup.sh)
RUN mkdir -p /system/persist/hlos_rfs/shared \
             /system/persist/rfs/mdm/adsp \
             /system/persist/rfs/mdm/mpss \
             /system/persist/rfs/msm/adsp \
             /system/persist/rfs/msm/mpss \
             /system/persist/rfs/shared

# Prefer ipv4 over ipv6
RUN echo "precedence ::ffff:0:0/96 100" >> /etc/gai.conf

# ldconfig
RUN ldconfig

# Rootfs development/eval tools
COPY ./userspace/eval/rootfs-init.sh /usr/local/bin/
COPY ./userspace/eval/rootfs-manifest.sh /usr/local/bin/
COPY ./userspace/eval/rootfs-diff.sh /usr/local/bin/
COPY ./userspace/eval/rootfs-export.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/rootfs-*.sh

# Read-only rootfs setup
COPY ./userspace/readonly_setup.sh /tmp/vamos/
RUN /tmp/vamos/readonly_setup.sh

# Drop non-English locales, man/doc/info pages, and xbps cache
RUN find /usr/share/locale -maxdepth 1 -mindepth 1 ! -name en_US ! -name en -exec rm -rf {} + && \
    rm -f /usr/lib/gcc/*/*/gnat1 /usr/lib/gcc/*/*/ada_target_properties \
          /usr/lib/llvm/*/bin/c-index-test \
          /usr/lib/llvm/*/lib/clang/*/include/opencl-c-base.h \
          /usr/lib/llvm/*/lib/clang/*/include/opencl-c.h \
          /usr/local/venv/lib/python*/site-packages/ffmpeg/install/include/libavutil/hwcontext_opencl.h \
          /usr/share/cmake-*/Help/module/FindOpenCL.rst \
          /usr/share/cmake-*/Modules/FindOpenCL.cmake \
          /usr/bin/run /usr/bin/lto-dump && \
    rm -rf /usr/share/man /usr/share/doc /usr/share/info && \
    rm -rf /var/cache/xbps

# ################# #
# #### Cleanup #### #
# ################# #

# DEBUG: allow root login
RUN echo ttyS0 >> /etc/securetty
RUN echo ttyMSM0 >> /etc/securetty

# DEBUG: fix comma password (chpasswd fails under QEMU)
# Use pre-computed hash for reliability: password is "comma"
RUN usermod -p '$6$vamosdev$NmMcideymvdAilEr.m7OeTX.fnt3H4UmcgheKB39rkrmebVfvZIUE17zjgtOxi7K07uGicamzFFbkFLE3EjQj/' comma && \
    usermod -p '$6$vamosdev$NmMcideymvdAilEr.m7OeTX.fnt3H4UmcgheKB39rkrmebVfvZIUE17zjgtOxi7K07uGicamzFFbkFLE3EjQj/' root

# DEBUG: SSH setup - sshd_config expects keys in /data/etc/ssh/
# The /data partition is mounted at runtime from /dev/sda12
# We need to create the ssh directory structure on first boot
RUN mkdir -p /var/chroot/ssh

# DEBUG: Custom sshd run script - /data is mounted in runit stage 1
RUN echo '#!/bin/sh' > /etc/sv/sshd/run && \
    echo 'exec 2>&1' >> /etc/sv/sshd/run && \
    echo '# Create ssh host keys if needed (on first boot)' >> /etc/sv/sshd/run && \
    echo 'mkdir -p /data/etc/ssh' >> /etc/sv/sshd/run && \
    echo 'if [ ! -s /data/etc/ssh/ssh_host_ed25519_key ] || [ ! -s /data/etc/ssh/ssh_host_rsa_key ] || [ ! -s /data/etc/ssh/ssh_host_ecdsa_key ]; then' >> /etc/sv/sshd/run && \
    echo '  rm -f /data/etc/ssh/ssh_host_*' >> /etc/sv/sshd/run && \
    echo '  ssh-keygen -A -f /data' >> /etc/sv/sshd/run && \
    echo 'fi' >> /etc/sv/sshd/run && \
    echo 'exec /usr/bin/sshd -D' >> /etc/sv/sshd/run

RUN ldconfig

# Note: /data mount is handled in runit stage 1 (/etc/runit/1)
