# syntax=docker/dockerfile:1.7
FROM debian:bookworm-slim AS native-build

ARG TARGETARCH
ARG LIBHYBRIS_COMMIT=7079712a42ea2754adf747e70c6cc75764c8596e
ARG AOSP_URL=https://dl.google.com/android/repository/sys-img/android/arm64-v8a-28_r02.zip
ARG AOSP_SHA1=e209114dd0dfc2f4e0d328f5fd7367fec39ee1bd

RUN test "$TARGETARCH" = "arm64" \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
      android-libcutils-dev android-platform-frameworks-native-headers \
      android-platform-system-core-headers autoconf automake ca-certificates \
      cmake curl e2fsprogs g++ gcc git libtool make pkg-config unzip \
    && rm -rf /var/lib/apt/lists/*

RUN git clone https://github.com/libhybris/libhybris.git /src/libhybris \
    && git -C /src/libhybris checkout "$LIBHYBRIS_COMMIT" \
    && cp -a /usr/include/android /src/android-headers \
    && printf '#include <android-version.h>\n' > /src/android-headers/android-config.h \
    && printf '#define ANDROID_VERSION_MAJOR 10\n#define ANDROID_VERSION_MINOR 0\n#define ANDROID_VERSION_PATCH 0\n#define ANDROID_VERSION_PATCH2 0\n#define ANDROID_VERSION_PATCH3 0\n' > /src/android-headers/android-version.h \
    && cd /src/libhybris/hybris \
    && ./autogen.sh --enable-arch=arm64 --with-android-headers=/src/android-headers --prefix=/out/hybris \
    && make -C common -j2 \
    && make -C include install \
    && make -C common install

COPY native/android_compat /src/android_compat
COPY native/hybris_probe /src/hybris_probe
COPY native/hybris_connect /src/hybris_connect
RUN cmake -S /src/android_compat -B /src/android_compat/build -DCMAKE_BUILD_TYPE=Release \
    && cmake --build /src/android_compat/build --parallel 2 \
    && mkdir -p /out/android-stubs \
    && cp /src/android_compat/build/libandroid.so /src/android_compat/build/liblog.so /out/android-stubs/ \
    && cmake -S /src/hybris_probe -B /src/hybris_probe/build \
      -DHYBRIS_INCLUDE_DIR=/out/hybris/include \
      -DHYBRIS_COMMON_LIBRARY=/out/hybris/lib/libhybris-common.so \
    && cmake --build /src/hybris_probe/build --parallel 2 \
    && cp /src/hybris_probe/build/okam-hybris-probe /out/ \
    && cmake -S /src/hybris_connect -B /src/hybris_connect/build \
      -DHYBRIS_INCLUDE_DIR=/out/hybris/include \
      -DHYBRIS_COMMON_LIBRARY=/out/hybris/lib/libhybris-common.so \
    && cmake --build /src/hybris_connect/build --parallel 2 \
    && cp /src/hybris_connect/build/okam-hybris-connect /out/

RUN curl -fsSL "$AOSP_URL" -o /tmp/aosp.zip \
    && echo "$AOSP_SHA1  /tmp/aosp.zip" | sha1sum -c - \
    && unzip -p /tmp/aosp.zip arm64-v8a/system.img > /tmp/system.img \
    && dd if=/tmp/system.img of=/tmp/system.ext4 bs=512 skip=2048 status=none \
    && mkdir -p /out/bionic \
    && debugfs -R 'dump -p /system/lib64/libc.so /out/bionic/libc.so' /tmp/system.ext4 >/dev/null \
    && debugfs -R 'dump -p /system/lib64/libm.so /out/bionic/libm.so' /tmp/system.ext4 >/dev/null \
    && debugfs -R 'dump -p /system/lib64/libdl.so /out/bionic/libdl.so' /tmp/system.ext4 >/dev/null \
    && debugfs -R 'dump -p /system/lib64/libz.so /out/bionic/libz.so' /tmp/system.ext4 >/dev/null \
    && debugfs -R 'dump -p /system/bin/linker64 /out/bionic/ld-android.so' /tmp/system.ext4 >/dev/null

FROM debian:bookworm-slim AS ffmpeg-build
ARG FFMPEG_VERSION=8.1.2
ARG FFMPEG_SHA256=9fd092511605bbebafe095ea6d38d9e40f34d12f7386e1258372df8be0576eb7
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl gcc libc6-dev make \
    && rm -rf /var/lib/apt/lists/*
RUN curl -fsSL "https://github.com/FFmpeg/FFmpeg/archive/refs/tags/n${FFMPEG_VERSION}.tar.gz" -o /tmp/ffmpeg.tar.gz \
    && echo "${FFMPEG_SHA256}  /tmp/ffmpeg.tar.gz" | sha256sum -c - \
    && tar -xf /tmp/ffmpeg.tar.gz -C /tmp \
    && cd "/tmp/FFmpeg-n${FFMPEG_VERSION}" \
    && ./configure \
      --prefix=/out/ffmpeg \
      --disable-everything \
      --disable-autodetect \
      --disable-doc \
      --disable-debug \
      --disable-network \
      --disable-avdevice \
      --disable-shared \
      --enable-static \
      --enable-small \
      --enable-ffmpeg \
      --enable-protocol=pipe \
      --enable-demuxer=h264 \
      --enable-parser=h264 \
      --enable-decoder=h264 \
      --enable-encoder=mjpeg \
      --enable-muxer=image2pipe \
      --enable-filter=scale \
      --extra-cflags=-Os \
      --extra-ldflags=-s
RUN cd "/tmp/FFmpeg-n${FFMPEG_VERSION}" \
    && make -j4 ffmpeg \
    && mkdir -p /out/ffmpeg/bin \
    && cp ffmpeg /out/ffmpeg/bin/ffmpeg

FROM python:3.12-slim-bookworm
ARG BUILD_VERSION=0.0.2
ARG BUILD_ARCH=aarch64
LABEL io.hass.version=$BUILD_VERSION \
      io.hass.arch=$BUILD_ARCH \
      io.hass.type="app" \
      io.hass.name="O-KAM Native Lab"

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates libstdc++6 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=native-build /out/hybris /opt/hybris
COPY --from=native-build /out/bionic /opt/bionic
COPY --from=native-build /out/android-stubs /opt/android-stubs
COPY --from=native-build /out/okam-hybris-probe /opt/okam/okam-hybris-probe
COPY --from=native-build /out/okam-hybris-connect /opt/okam/okam-hybris-connect
COPY --from=ffmpeg-build /out/ffmpeg/bin/ffmpeg /usr/bin/ffmpeg
COPY tools/fetch_official_sdk.py /opt/okam/tools/fetch_official_sdk.py
COPY docs/vendor-artifacts.json /opt/okam/docs/vendor-artifacts.json
COPY src/okam_native /opt/okam/okam_native
COPY okam_native_app/app_entrypoint.py /opt/okam/app_entrypoint.py

EXPOSE 8099
ENTRYPOINT ["python3", "/opt/okam/app_entrypoint.py"]
