# Development status

## Accepted evidence

- The physical shared camera completed two sanitized official-WebViewer traces.
  Both showed the same 17-call initialization, connection, live-start,
  live-stop, callback cleanup, and shutdown sequence.
- The trace recorder stored only function names, argument shapes, opaque object
  labels, string lengths/correlation tags, and scalar values. The local trace
  passed the repository's public-safety scanner and remains ignored.
- The tiny 32-bit console helper loaded the official P2P DLL and found every
  required export. It then initialized and tore down `DevDll_925.dll` using the
  securely stored server parameter without WebViewer or a GUI.
- The official SDK archive matched its pinned SHA-256. Both required ARM64
  vendor libraries were extracted and their dependency closure is complete.
- The minimal libhybris plus API 28 Bionic closure loads the official ARM64
  library and resolves both the PPCS/JNI surface and the non-JNI client API.
- The secondary account enumerates exactly one shared camera without WebViewer.
  Its virtual ID resolves through the official fixed host, and the official P2P
  directory returns the initialization parameter without logging either value.
- Both official wake endpoints accepted a bounded request for the physical
  camera. The ARM64 native helper then reached `ONLINE` state 3 against that
  camera and performed a clean disconnect under ARM64 emulation.
- O-KAM Native Lab 0.0.6 passed on the physical Raspberry Pi: account
  enumeration, both wake services, native P2P state 3, camera authentication,
  an Annex-B H.264 keyframe, official stream stop, and clean disconnect.
- The 0.0.7 candidate fed the physical camera's native H.264 through a
  checksum-pinned minimal FFmpeg build containing only H.264 decode, MJPEG
  encode, pipe/image2pipe, and the required scale filter. It produced a valid
  2304x1296 in-memory JPEG and disconnected cleanly without persisting imagery.

## Not yet accepted

- Persistent native stream reuse, viewer reference counting, idle disconnect,
  Home Assistant camera API compatibility, and clean long-running service
  shutdown remain unaccepted.

Version 0.2.0 remains blocked by the machine-readable physical gates. The
repository version stays at 0.0.x while these items are under development.
