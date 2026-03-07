### Fix AMD Radeon 890M (gfx1150) support in Jellyfin container
### Required for VAAPI hardware transcoding and HDR tone mapping on newer AMD iGPUs
### - Install Mesa 25+ drivers from backports (adds gfx1150 support)
### - Install Rusticl OpenCL from backports (GPU-accelerated HDR tone mapping)
### - Install latest LLVM from backports (required for shader compilation)
### - Replace Jellyfin's bundled radeonsi driver with Mesa's newer version

### AMD Ryzen AI 9 HX PRO 370 (Strix Point) w/ Radeon 890M (RADV GFX1150)
### - Decode: H264, HEVC, HEVC 10bit, AV1, VP9, JPEG
### - Encode: H264, HEVC, HEVC 10bit, AV1

FROM jellyfin/jellyfin:latest

RUN set -ex && \
    # Detect Debian codename and add backports repository
    DEBIAN_VERSION=$(grep VERSION_CODENAME /etc/os-release | cut -d= -f2) && \
    echo "Detected Debian version: $DEBIAN_VERSION" && \
    echo "deb http://deb.debian.org/debian ${DEBIAN_VERSION}-backports main" >> /etc/apt/sources.list.d/backports.list && \
    apt-get update && \
    # Find the newest available LLVM version in backports
    LLVM_PKG=$(apt-cache search --names-only '^libllvm[0-9]+$' | grep -o 'libllvm[0-9]\+' | sort -V | tail -n1) && \
    echo "Found newest LLVM package: $LLVM_PKG" && \
    # Install Mesa drivers, OpenCL (Rusticl), and dependencies from backports
    apt-get install -y -t ${DEBIAN_VERSION}-backports \
        mesa-va-drivers \
        mesa-vulkan-drivers \
        mesa-opencl-icd \
        libva2 \
        libva-drm2 \
        libdrm2 \
        libdrm-amdgpu1 \
        ${LLVM_PKG} && \
    # Replace Jellyfin's bundled radeonsi driver with Mesa's version
    GALLIUM_LIB=$(find /usr/lib/x86_64-linux-gnu/ -name "libgallium*.so" -type f | sort -V | tail -n1) && \
    [ -n "$GALLIUM_LIB" ] && [ -f /usr/lib/jellyfin-ffmpeg/lib/dri/radeonsi_drv_video.so ] && \
    cp "$GALLIUM_LIB" /usr/lib/jellyfin-ffmpeg/lib/dri/radeonsi_drv_video.so && \
    echo "Replaced radeonsi driver with $GALLIUM_LIB" && \
    # Cleanup
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*
