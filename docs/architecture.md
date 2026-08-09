# Architecture and evidence gates

The target runtime is a single ARM64 Home Assistant app. It downloads the
checksum-pinned official Flutter SDK during its build, starts only when Home
Assistant requests media, copies H.264 without transcoding, and disconnects the
battery camera after the last viewer leaves.

The development path has three independently testable layers:

1. `tools/trace_webviewer.py` records a redacted call graph from the already
   working official Windows process. Values that may be account, camera, token,
   password, or CGI parameter data are never written verbatim.
2. `native/windows_probe` dynamically loads the official 32-bit DLLs. It begins
   with a non-networking export/version inventory; connection calls are added
   only after their signatures are confirmed by a sanitized trace.
3. `native/arm64_probe` validates that the official ARM64 Android library and a
   separately supplied Bionic runtime load, then checks for the exact P2P/JNI
   symbols proven on Windows. Vendor binaries are never committed.

The Windows helper is a protocol oracle, not the product. The product cannot be
released as 0.2.0 until `okam-acceptance` passes on the physical Raspberry Pi
and camera. The gate explicitly rejects Wine, WebViewer, and Xvfb.

## Bionic boundary

`libOKSMARTPPCS.so` is an Android/Bionic ELF and cannot safely be treated as a
glibc plugin. Its measured vendor closure is `libOKSMARTPPCS.so` plus
`libvp_log.so`; its Bionic system closure is `linker64`, `libc`, `libdl`,
`libm`, `libz`, `liblog`, and `libandroid`. `tools/validate_bionic_root.py`
checks that exact AArch64 closure and records hashes and total size. We will not
paper over Bionic internals with guessed ABI stubs.

The accepted loader uses only `libhybris-common`, real API 28 AOSP
`libc/libm/libdl/libz` plus `linker64`, and measured leaf stubs for `libandroid`
and `liblog`. The AOSP source archive is checksum pinned and used only while
building; the final runtime closure is approximately 3.4 MB and contains no
Android container or framework.

Account discovery is deliberately separate from P2P transport. The service
reproduces the official WebViewer sequence against the fixed
`https://api.eye4.cn` origin: account summary, token login, then device list.
Only the local alias and count cross the status API; vendor identifiers,
passwords, and tokens remain in process memory and are never logged.
