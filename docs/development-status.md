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

## Not yet accepted

- The first direct console streaming trial safely accepted the official
  connection configuration but timed out waiting for the camera. At the same
  time, the previously proven low-power wake service was unreachable. This is
  a failed physical acceptance trial, not proof that the console ABI or camera
  connection works.
- No Bionic runtime has been selected or shipped. The measured seven-file
  Bionic system closure must load the two vendor libraries before connection
  work is ported.
- Account login and shared-camera enumeration still need a native client. The
  development credential captured from the official process is not a
  production setup mechanism.

Version 0.2.0 remains blocked by the machine-readable physical gates. The
repository version stays at 0.0.x while these items are under development.
