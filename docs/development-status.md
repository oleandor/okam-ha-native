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

## Not yet accepted

- The native wake/connect/disconnect path still needs the same successful result
  from the Home Assistant app on the physical Raspberry Pi.
- Camera-level `admin` authentication and its response callback are not yet
  accepted through the native helper.
- Native H.264 receive, snapshot, on-demand reuse, idle disconnect, and clean
  service shutdown remain unaccepted.

Version 0.2.0 remains blocked by the machine-readable physical gates. The
repository version stays at 0.0.x while these items are under development.
