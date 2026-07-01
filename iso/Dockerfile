FROM debian:bookworm

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
      ca-certificates \
      debian-archive-keyring \
      live-build \
      xorriso \
      isolinux \
      syslinux-common \
      squashfs-tools \
      rsync \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace/iso

CMD ["bash", "./build-iso.sh"]
