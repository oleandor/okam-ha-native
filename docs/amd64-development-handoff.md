# amd64 development handoff

This document describes the unfinished `amd64` implementation on the
`codex/amd64-support-wip` branch. Do not merge or release this branch until the
physical live-stream gate in this document passes.

No account credentials, camera identifiers, service parameters, IP addresses,
or captured camera payloads are included here or in the branch.

## Goal

Publish one lightweight Home Assistant app for both supported 64-bit
architectures:

- `aarch64`: keep the proven official ARM64 transport library behind the small
  Bionic/libhybris compatibility layer.
- `amd64`: use a native pure-Python implementation of the same encrypted
  CS2/PPPP UDP transport. Do not add emulation, Wine, Android, or a graphical
  runtime.

The account API, wake service, bridge HTTP API, MediaMTX integration, snapshots,
idle lifecycle, and Home Assistant custom integration remain shared.

## Code added or changed

- `src/okam_native/cs2.py` implements directory lookup, direct UDP punching,
  relay negotiation, packet encryption, reliable data channels, camera command
  framing, authentication, and H.264 frame parsing.
- `src/okam_native/amd64_helper.py` implements the same stdin/stdout helper
  contract used by the existing ARM64 helper.
- `native/amd64_connect/okam-amd64-connect` is the executable entry point used
  by the amd64 container.
- The app Dockerfile selects an architecture-specific final stage while sharing
  the runtime and minimal FFmpeg build.
- The publish workflow builds `linux/arm64` and `linux/amd64`, then creates the
  versioned and `latest` multi-architecture manifests.
- App and integration metadata are prepared for version `1.2.0`.
- Missing camera-level credentials are handled by using an explicit
  `camera_password` override, the enumerated camera credential, or the camera's
  common initial value, in that order. This also addresses GitHub issue #5.

## Post-handoff corrections

These are transport-layer fixes and instrumentation. No packet bytes on the
wire changed, so they do not invalidate the byte-for-byte comparisons above.

1. Login candidates each get their own bounded read window
   (`AUTH_CANDIDATE_SECONDS`). They previously shared one deadline, so a later
   candidate could be starved of time and report a rejection that was really a
   timeout. This alone can explain part of the "authentication results have
   varied" observation.
2. A login outcome now distinguishes *rejected* (a result was returned) from
   *silent* (no response arrived). `CameraLogin.attempts` records one entry per
   candidate and `CameraLoginRejected` carries the same tuple on failure.
3. `read_command` bounds the header and body reads against one shared deadline.
   A consumed header with a missing body now fails hard instead of raising a
   soft timeout, because channel 0 cannot be resynchronized once a partial
   command has been read. Retrying after that would have read into the
   following command.
4. The live-start acknowledgement is read and recorded before the first media
   read, instead of being left unclaimed in the channel-0 buffer where a later
   read could misattribute it. Media buffers during that bounded wait, so no
   frames are lost.
5. `CS2Session.counters` records sanitized packet counts only — no addresses,
   payloads, or credentials. `CS2Session.connect_path` records which branch
   established the peer (`direct-punch`, `direct-accept`, or `relay`).

### Reading the new helper summary

The helper JSON now also carries `login_candidate`, `login_attempts`,
`connect_path`, `stream_start_command`, `stream_start_result`, and `counters`.
On the next physical run these separate the remaining hypotheses directly:

- `channel1_packets` / `channel1_bytes` at zero means the camera sent no media
  at all, so the problem is upstream of framing.
- `channel1_bytes` above zero while no frame is parsed means media is arriving
  and `read_video_frame`'s 32-byte header or magic is wrong.
- `packets_from_other_source` above zero means the camera is sending from a
  port that is not the latched peer, and those packets are being discarded.
- `packets_undecodable` or `packets_length_mismatch` rising during the stream
  window points at the encryption or envelope selection for media packets.
- `login_candidate` identifies which credential class the camera accepted
  without disclosing the value. Candidate 2 is the empty password: note that
  `make_cgi_request` also carries a trailing fixed pair, matching the official
  command, so an empty-password success does not prove the stream request is
  authorized the same way.

## Measured amd64 evidence (development host, direct UDP)

Captured with `tools/local_probe.py` against the physical camera. Counts only.

| Stage | Result |
| --- | --- |
| connect | `connected`, `connect_path=direct-punch`, clean disconnect |
| authenticate | `login_attempts=-1,-1,0`, `login_candidate=2` |
| stream | `stream_start_command=0x60D1`, `stream_start_result=0`, **no channel-1 data** |

What this settles:

1. Candidates 0 and 1 are **genuinely rejected** with result `-1`; only the
   empty password returns `0`. These are real responses, not read timeouts, so
   the shared-deadline defect was not the cause of the varying results.
2. `channel1_packets` and `channel1_bytes` never appear, so the camera sends no
   media at all. The media framing in `read_video_frame` is not implicated, and
   neither is a missing TCP relay: the session is `direct-punch` throughout.
3. Live-start is answered `0x60D1` result `0` while media never starts, so a
   result of `0` here does not mean the camera considers the session ready.
4. `other_f141` counted 14 inbound `F1 41` packets **after** the session was
   established, all ignored by the receive loop. The camera is still asking to
   complete the readiness handshake.
5. `F1 42` and `F1 43` were in neither wire-envelope set, so the readiness
   reply was the only session packet sent in clear while data, acknowledgement,
   alive, and close packets were all encrypted. This is the leading explanation
   for finding 4 and is now sent dual-wire like `F1 41`.
6. `packets_from_other_source` was non-zero (6) in one authenticate run and
   absent in others, so it is real but intermittent and is not the cause of the
   missing media.

Changes made in response, one at a time:

- `F1 42`/`F1 43` are now sent dual-wire. Inbound `other_f141` fell from 14 to
  6 across comparable runs, so the camera re-punches less, but readiness is
  still not mutual and media still does not start.
- `F1 41` repeats are now answered while connected (`punch_repeats`). Not yet
  measured against the camera: the runs after this change failed to connect.

Sessions run back to back fail to connect at all, which matches the existing
note about rapid reconnects. Two consecutive failures were also seen after a
two-minute rest. Neither change can affect connect, because both only alter
packets sent after the session exists while `connect()` runs its own loop.

### Results on a second, otherwise idle account

Re-measured against a freshly created account, so no other client held the
camera. Connect became reliable again, which supports session contention or
throttling as the cause of the earlier connect failures rather than any code
change.

7. Answering `F1 41` repeats produced `other_f143` for the first time: the
   camera acknowledges the readiness reply, so the handshake now completes in
   both directions. Media still does not start.
8. Forcing live-start with the enumerated credential while skipping the login
   probe still returns `0x60D1` result `0`, and still yields no media. The
   credential is therefore **not** what gates the media channel, and an
   accepted login is not a precondition for an accepted live-start.
9. `substream=0` behaves the same as `substream=2`: accepted, no media.
10. Packet accounting is now complete. In one run `pump_packets` (100) equalled
    the sum of every branch counter, and no channel above 0 ever appeared. The
    camera sends nothing on any data channel except channel 0, and no
    unrecognized packet type other than `F1 43` and `F1 E1`. Media is not
    arriving and being misparsed; it is never sent.

## Resolved: media requires the relay session

A sanitized packet capture of the official aarch64 helper on the control host
(`tools/pi_control_capture.py`, summarized by `tools/pcap_summary.py`) settled
it. In the official session:

- Not one inbound `F1 41` was seen. The direct punch never succeeds there; the
  client floods punches at candidate addresses, gets no answer, and falls back.
- The relay request `F1 80` is addressed to the **directory servers**, which
  answer `F1 81` and then `F1 82`. It is never sent to the relay itself.
- The data session runs with the relay: `F1 83` both directions, `F1 84`
  ready, then `F1 D0` payloads.

Two corrections followed, each with a deterministic test:

1. `F1 80` now goes to the directory endpoints, in both the `F1 73` branch and
   the periodic resend. Previously it went to the relay endpoints, which never
   produced `F1 82`, so the relay path could never complete.
2. `CS2Session(prefer_relay=True)` is the default. A direct punch produces a
   session that authenticates and acknowledges live-start but never streams, and
   it otherwise wins the race and hides the relay path entirely.

Measured result on amd64, twice, including with no experiment flags:

```
connect_path=relay  login_candidate=0  login_attempts=[0]  login_result=0
channel1_packets=1237  channel1_bytes=1184793
h264_frames=3  h264_bytes=156727  keyframe_seen=true
stream_stop_sent=true  disconnected=true
```

Release gates 1 through 6 are therefore met on amd64. Note also:

- Over the relay the **enumerated credential authenticates on the first
  attempt**. The earlier "only the empty password works" result was an artefact
  of the direct-punch session, not a credential problem.
- Live-start is answered with command `0x6037` on the relay path, where the
  direct path used `0x60D1`. Both are accepted.

## Known remaining issue: connect intermittency

After many sessions in quick succession the relay stops answering `F1 84`, and
connect fails even after several minutes of rest. The negotiation histogram
shows the stall precisely: `connect_f169`, `connect_f171`, `connect_f173`,
`connect_f181`, and `connect_f182` all arrive with correct lengths
(`f182_len24`, `f173_len12`), so the request is accepted and punches are sent,
but no `F1 84` returns. During these failures the camera floods `F1 41` and
`F1 42`, trying to force a direct session.

This is not a code regression: the connect path is unchanged since the
successful runs.

It is also **not** contention with another bridge. The hypothesis was tested
directly: with the only other consumer of this shared camera stopped for
eleven minutes, the first run succeeded with a very clean negotiation
(`connect_f182=15`, `connect_f184=1`, 291 packets total), and the two
back-to-back runs immediately after it both failed in the usual way
(`connect_f182≈200`, no `F1 84`, ~2000 packets). The camera is shared to two
accounts and the other bridge was idle for all three runs.

The pattern is a per-camera cool-down that our own preceding session causes:

- A rested camera accepts the relay rendezvous almost immediately.
- Sessions soon after a previous one stall, and during the stall the camera
  floods `F1 41` and `F1 42`, which the relay-preferring connect loop ignores.
- A successful run shows no inbound `F1 41`/`F1 42` at all.

The leading explanation is that the previous session is still registered for
this camera UID, so a new rendezvous is accepted (`F1 81`, `F1 82` arrive) but
the camera never punches the relay because it is still bound to the old
session, and instead tries to resurrect the direct path with us.

### Largely resolved by two teardown corrections

1. `close()` now notifies every endpoint the session touched, not just the
   peer. A connect that never latched a peer previously sent no close at all,
   so declined direct punches were left holding a stale binding.
2. A declined direct readiness request (`F1 42`) is now acknowledged with
   `F1 43` during relay-preferring connect, without adopting the sender as the
   session peer. Leaving it unanswered made the camera retry indefinitely
   rather than release its previous binding.

Measured after both changes: four consecutive `--stage stream` runs with no
rest between them all returned exit `0` over the relay, where previously only
the first run after a long idle period succeeded.

The behaviour is not perfect. A fifth run degraded again, and the degraded mode
has a reliable signature worth using as a health check: the enumerated
credential is rejected, the empty password returns a bogus `result=0`, and no
media follows. **`login_candidate != 0` predicts a session that will not
stream**, and is a better readiness test than the live-start result.

**Product implication:** if a camera needs minutes between P2P sessions, the
bridge must hold one session open rather than reconnect per request. The
existing warm-hold and idle-disconnect behaviour should be re-checked against
this constraint before release.

## Protocol findings already established

1. The official transport probes the advertised UDP port plus or minus three.
   The pure client now uses the same bounded range.
2. The camera uses a mixed clear/encrypted CS2 wire format. The packet classes
   and recovered cipher vectors are covered by unit tests.
3. A camera command must be written as one atomic DRW channel-0 payload. The
   official API accepts its header and body separately but coalesces them before
   emitting the transport packet.
4. After receiving `F1 41`, the client must send `F1 42` with the camera UID and
   an `F1 E0` alive packet before treating the direct session as ready.
5. A successful official session reported connection mode `2`, but sanitized
   socket instrumentation observed no TCP traffic. The working stream used a
   direct UDP peer, so implementing a speculative TCP relay is not the next
   step.
6. The reconstructed relay request is structurally identical to the official
   request: header, UID, reversed address, port, and rendezvous token all match.
7. The reconstructed login command has matched the official command byte for
   byte in sanitized comparisons.

## Physical results so far

The existing `aarch64` release remains proven on a Raspberry Pi 4, including
enumeration, wake, P2P connection, camera authentication, H.264 media,
snapshots, continuous streaming, and clean disconnect.

On a local `amd64` Home Assistant/Docker test host:

- Account enumeration and wake work.
- The pure client can establish a direct encrypted UDP session, although rapid
  reconnects are intermittent.
- The camera acknowledges reliable channel writes.
- Authentication results have varied with camera state during rapid repeated
  tests. In one representative run the enumerated and common initial passwords
  were rejected while the empty-password candidate returned result `0`.
- The live-start command has returned command `0x60D1` with result `0` after
  that authentication path, but channel 1 did not deliver video frames.
- The official helper has streamed at least three H.264 frames, more than 1 KiB,
  including a keyframe, over direct UDP from the same development host.
- Repeating the same official login rapidly could also alternate between
  success and rejection, suggesting camera wake/session state or throttling is
  a factor. Do not assume every authentication rejection proves the command
  bytes are wrong.

The unresolved problem is therefore after transport establishment and around
authentication/session readiness/live-start sequencing or timing. The evidence
does not currently point to a missing TCP relay implementation.

## Recommended next investigation

1. Start with a rested camera and one test session. Avoid rapid reconnect loops.
2. Wake the camera, wait for it to become responsive, and run the official
   helper once as a control.
3. Run the pure helper once with the same enumerated credential.
4. Record only sanitized protocol structure: packet type, channel, sequence,
   payload length, command ID, response result, timing, and state transitions.
   Never record or publish packet bodies, credentials, identifiers, service
   parameters, tokens, or endpoints.
5. Compare the ordered official and pure sequences from `F1 41` through the
   first channel-1 frame. Focus on missing readiness acknowledgements,
   unconsumed channel-0 responses, required delays, sequence initialization,
   and live-start response handling. Start from the `counters` block described
   under "Reading the new helper summary" — it narrows this to one branch
   before any packet-level comparison is needed.
6. Change one behavior at a time and add a deterministic unit test for every
   protocol correction.

## Known follow-ups

Not defects that block the gates, but real rough edges observed while testing.

### A bridge restart invalidates a live view already open

`CameraBridge` generates a fresh `_stream_token` on every process start, and
Home Assistant caches the stream URL inside the `Stream` object it built for the
entity. After the bridge restarts, that cached URL keeps the old token and the
stream worker fails with `401 Unauthorized` until the config entry is reloaded
or a new stream is created. Reproduced directly: restarting the bridge container
produced repeated `Unauthorized error opening stream` in the worker log, and
reloading the config entry cleared it.

Two ways to fix, neither attempted yet:

- Derive the stream token from a value that survives a restart, so a cached URL
  stays valid. Simplest, but it makes the token only as fresh as the process
  configuration.
- Have the integration notice the failure and call the stream's
  `update_source()` with a freshly fetched URL. More correct, and it also covers
  the token changing for any other reason.

### The camera entity state lags the bridge

The coordinator polls bridge status on its own interval, so `camera.*` can still
read `idle` while media is actively flowing. Snapshots and live view both work in
that window, so this is cosmetic, but it makes the entity state a poor signal for
automations that want to know whether the camera is streaming.

## Required release gates

Current status: gates 1 to 7 are met. Gates 1 to 6 were verified repeatedly on
a development host. Gate 7 was verified with a locally built `linux/amd64`
image: the bridge HTTP endpoint served an Annex-B stream containing SPS, PPS,
and a keyframe, and produced a 2304x1296 JPEG snapshot.

Gate 7 initially failed in the container while the same code worked on the
host, and the cause was worth recording. The receive loop matched the session
peer on address **and port**, so media arriving from a neighbouring source port
was discarded. Behind container NAT that was the entire media channel: the
session reached the relay and authenticated, then counted 272 packets as
foreign and delivered no video. The same defect showed up as a handful of
dropped packets on the host, which is why it looked negligible for so long.
Inbound packets are now matched on the peer host, and the port drift is
counted.

Gate 8 is met. On a local amd64 Home Assistant installation the config flow
creates the camera entity, `camera/stream` returns an HLS playlist whose
segments are real fMP4 media, and the camera proxy returns a JPEG. Reaching it
required two fixes worth remembering:

- The bridge advertised a raw Annex-B stream, which carries no timestamps.
  Home Assistant rejects that with "No dts in N consecutive packets", so live
  view never played while snapshots kept working. The advertised source is now
  MPEG-TS, muxed without re-encoding.
- The image's FFmpeg is built with `--disable-everything`, so it had no mpegts
  muxer and the muxer exited immediately. Any future change to that build must
  keep the muxers the bridge depends on.

Gate 9 is met. A `1.2.0-test1` build was installed on the physical Raspberry
Pi 4 as a local app beside the released one, on a separate port and slug. The
official aarch64 transport loaded, the account enumerated, the bridge came up,
and the camera entity produced snapshots and **played live video**, with a
faster second open from the cached keyframe.

Three configuration defects surfaced only on real hardware and are fixed:

- The camera password option could not be left empty. It was declared required,
  and its null default kept the key present and invalid even once optional, so
  the supervisor refused to save with "Missing required option". No placeholder
  was safe either, since any value overrides the enumerated credential. This
  is the same path as issue #5.
- Editing an installed app's `config.yaml` does not rebind its schema, so the
  app must be reinstalled after such a change.
- A watchdog pointing at a host port rather than a declared container port never
  resolves, and the supervisor restarts the app underneath a live stream. This
  affected only the test app configuration.

Gates 10 and 11 remain. Gate 10 passes on every push. Gate 11 needs a release

 — a real
amd64 Home Assistant installation with the integration configured, the physical
Raspberry Pi, CI, and a registry push.

**Gate 9 now matters more than it did.** The relay work is confined to
`cs2.py`, which is amd64-only, but the live-view priming changed
`session.py`, which both architectures share. The aarch64 regression is
therefore no longer a formality.

All of these must pass before merging or releasing `1.2.0`:

1. Pure amd64 helper connects to the physical camera.
2. Authentication response is received with result `0`.
3. Live-start is sent and acknowledged.
4. At least three valid H.264 frames and 1 KiB of H.264 payload are received.
5. An H.264 keyframe is observed.
6. Live-stop is sent and the P2P session disconnects cleanly.
7. The clean amd64 app image exposes an Annex-B H.264 stream through the bridge
   HTTP endpoint.
8. The Home Assistant camera entity displays live video and produces a JPEG
   snapshot on the amd64 test installation.
9. A clean aarch64 image still passes the physical Raspberry Pi 4 stream and
   snapshot regression test.
10. The full unit test suite and GitHub Actions workflow pass.
11. The published GHCR version tag and `latest` tag contain both `linux/amd64`
    and `linux/arm64` manifests.

## Test status at handoff

- The complete unit test suite passes: 66 tests.
- The CS2-focused subset passes: 26 tests.
- `git diff --check` passes.
- Python compilation across source, integration, app, tests, and tools passes.
- Temporary development probes and build directories are intentionally not
  part of the branch.
