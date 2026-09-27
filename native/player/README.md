# Gravitas player engine

Gravitas' own media player, in Rust: FFmpeg demuxes and decodes, the audio
output drives the clock, and the app pulls video frames at its own frame rate.
It sits behind the same `MediaPlayer` port as libmpv and is opt-in while it
works towards parity (see the roadmap below).

```
crates/engine/         gravitas-player-engine  the engine; no Python, no Qt
crates/libass-sys/     libass-sys              libass bindings, generated from the installed headers
crates/libplacebo-sys/ libplacebo-sys          libplacebo bindings, plus its FFmpeg helpers compiled as C (not on macOS)
crates/python/         gravitas-player-python  PyO3 module `gravitas_player`
```

## Build and run

Needs a Rust toolchain, a C compiler and libclang (bindgen), and the
development files of FFmpeg (libavformat, libavcodec, libavutil,
libswresample, libswscale), libass and libplacebo, plus ALSA's on Linux:

```bash
sudo dnf install ffmpeg-devel libass-devel libplacebo-devel alsa-lib-devel clang-devel   # Fedora (RPM Fusion)
sudo apt install libavformat-dev libavcodec-dev libswresample-dev libswscale-dev libass-dev libplacebo-dev libasound2-dev libclang-dev
brew install ffmpeg libass                           # macOS (libplacebo, molten-vk: tests only, see below)

uv run python scripts/build_native_player.py         # from the repo root
uv run gravitas
```

On Windows the same libraries come from MSYS2's UCRT64 environment, whose
Rust links against them the way Linux's does, through pkg-config; the script
puts `C:\msys64\ucrt64\bin` on PATH itself (`MSYS2_ROOT` moves it):

```bash
pacman -S mingw-w64-ucrt-x86_64-{rust,pkgconf,clang,binutils,ffmpeg,libplacebo,libass,vulkan-headers}   # in an MSYS2 shell
pacman -S make diffutils mingw-w64-ucrt-x86_64-{gcc,nasm,dav1d,ffnvcodec-headers,zlib}                  # for the lean FFmpeg
uv run python scripts/build_ffmpeg_windows.py                                  # the lean FFmpeg (optional, see below)
uv run python scripts/build_native_player.py                                   # from the repo root, any shell
uv run python scripts/build_video_bridge.py --qt C:/Qt/6.11.2/msvc2022_64      # Vulkan zero-copy (MSVC)
```

The script builds the module and copies it to
`src/gravitas/infrastructure/player/gravitas_player.abi3.so` (gitignored;
`gravitas_player.pyd` on Windows, with the MinGW DLLs it links gathered into
`gravitas_player.libs/` beside it), where the adapter imports it. On Windows
that is 104 DLLs and 153 MiB with MSYS2's FFmpeg, which links every codec
library MSYS2 packages and is GPL; `build_ffmpeg_windows.py` builds the same
release with only what the engine uses (decoders, demuxers, protocols, the
Windows hwaccels, dav1d, zlib, schannel), LGPL, and with it the module takes
32 DLLs and 51 MiB. The engine script uses it whenever it is there. It is then
the default player: Settings > Player switches to mpv (from the next start),
and `GRAVITAS_PLAYER=native|mpv` overrides the setting. Without the module, playback goes through mpv
whatever the setting says, and Settings says so.

## Checks

```bash
cargo fmt --all --check
cargo clippy --workspace --all-targets     # pedantic, warnings are findings
cargo test --workspace                      # end-to-end tests need the ffmpeg CLI
```

`crates/engine/tests/playback.rs` generates a real file with the `ffmpeg`
CLI and plays it through the engine on the silent audio output; it skips
itself when the CLI is missing. On macOS the tests also need `brew install
libplacebo molten-vk`: libplacebo through MoltenVK is the reference the
Metal renderer's tests compare against (`metal::tests`). The app never links
it there. The Python side has its own tests under
`tests/infrastructure/player/` (`test_native_player.py` against a fake,
`test_native_engine.py` against the built module).

## Why an engine of our own

libmpv has carried Gravitas a long way, and nearly every hard problem in
CLAUDE.md is a workaround for something it cannot do from inside a client:

- **Dolby Vision profile 5 plays magenta.** libmpv's render API offers only
  `OPENGL` and `SW`; libplacebo (`vo=gpu-next`), which converts IPT-C2, is not
  reachable through it at all. No backend change inside mpv fixes this.
- **Switching an audio or subtitle track is a network operation.** mpv drops
  the packets of streams it is not playing, so a switch re-reads the stream.
  The hot-audio `lavfi-complex` graph works around it, and that graph in turn
  overflows on sparse tracks and silences embedded subtitles.
- **Rendering needs a native bridge per platform** (the Vulkan and Metal video
  bridges), because the render API speaks OpenGL only.
- **One HTTP connection per file**, which the stream proxy exists to fix.

An engine of our own removes these limits at the source: it keeps packets of
every track, renders through libplacebo on Qt's own Vulkan device, and owns
its network layer. It is also a large piece of software, so it is built in
milestones behind the existing `MediaPlayer` port, and mpv remains the default
until the native engine reaches parity.

## How it works

Threads per loaded file, all joined on `stop()`:

- **demux** — opens the URL through FFmpeg (headers, reconnect options and a
  read timeout on the AVIO layer, an interrupt callback so `stop()` never waits
  on the network), routes packets into bounded per-kind packet queues, and
  serves seek requests. It reads ahead until `READAHEAD_S` of the file or
  `MAX_QUEUED_BYTES` are queued, and never exits at end of file: a seek after
  the end is ordinary.
- **video decode** — packets → frames in a small frame queue.
- **audio decode** — packets → frames → one resampler to the output's rate and
  channel count → timestamped chunks.
- **events** — delivers `Event`s to the sink, so no engine thread ever waits on
  the embedder (and the Python side never holds an engine lock with the GIL).
- **monitor** — derives `loading`, `ended` and the read speed from shared
  state and emits changes.

One audio output lives for the player's lifetime (cpal; a `Null` output that
consumes samples in real time exists for tests and machines without a device).

**Serials, not flushes.** Every seek bumps an atomic serial. Packets, chunks
and frames carry the serial they were produced under, and each stage drops
anything older on sight — so a seek never has to wait for a pipeline to drain.

**Audio is the master clock.** The output callback knows the pts of the
sample it writes and the instant it will be heard (cpal's playback
timestamp), and publishes `(pts, instant)`; the clock extrapolates from it.
An underrun freezes the clock, which is exactly buffering: video waits for it.
Files without audio (or after audio ends) run on the wall clock.

**Preroll.** After a load or a seek, nothing plays until the first video frame
and the first audio chunk of the new serial exist (or one of them is known to
be absent). The picture on screen during a paused seek is that first frame.

**Subtitles are decoded for every track, drawn for one.** Every subtitle
stream's packets go to one decoder thread -- they are tiny -- so each track
keeps its events and switching between them is instant. Text formats reach
libass as the ASS events FFmpeg's decoders make of them (deduplicated,
because a decoder numbers events afresh after a seek), with the file's
attached fonts; image formats (PGS, VobSub) are kept as palette indices and
scaled onto the frame. The viewer's style applies to text tracks that carry
no styling of their own, as mpv's defaults do. A subtitle file loaded beside
the video (an addon's) becomes one more track. Because every track's events
are counted as they arrive, a track picked by language that turns out to say
nothing -- a forced-only track labelled as a plain default one, which one DUAL
WEB-DL of South Park has -- gives way once to a same-language track that is
talking. A track the viewer picked is never second-guessed.

**Every audio track is read, one is decoded.** The demuxer keeps each audio
track's packets (`audio_queue.rs`), from `KEEP_BEHIND_S` behind the
playhead to wherever it has read: compressed audio is small, and the file
carries every track anyway, so this costs memory, not bandwidth. The decoder
reads the selected track through a cursor instead of taking packets out,
which leaves the history a switch back needs. A switch between two tracks
while playing is then local: the new track's decoder goes into the packet
stream ahead of its first packet (so no packet ever meets the wrong
decoder), positioned at the playhead; every switch bumps an audio
generation, and the mixer drops what the old track had queued, keeps the
clock running through the few milliseconds before the new track's first
audio (`SWITCH_GRACE`), and trims that audio to the moment it is heard. No
seek, no re-read, no stall: the new track fills the output within ~50 ms in
the tests, where the old seek-and-re-read path froze the picture for 2.1 s
on a real stream. Turning audio off or on still seeks, which is rare and
changes whether audio drives the clock at all. Unlike mpv's hot-audio graph
there is no track cap and no sparse-track hazard: only the selected track
is decoded, and the others never hold the demuxer back.

**The engine reads network streams itself** (`src/net/`), doing for
itself what the app's Python stream proxy does for mpv, without the proxy in
the path: FFmpeg reads through custom I/O (`avio.rs`), so a seek is a new
read position, not a new connection, and the reader's position is known
exactly rather than inferred from connections. `CONNECTIONS` worker threads
fetch runs of chunks on the proxy's aligned 1 MiB grid, one ranged request
per run over a pooled `ureq` agent (connections, and their TLS, are
reused). A read takes bytes from a chunk still arriving, so a header costs a
round trip rather than a mebibyte. The chunk cache is the proxy's: on disk,
2:1 ahead:behind the reader, evicted by distance from it, removed with the
stream. The proxy's rules carry over -- never guess (a host without ranges,
or a probe that failed for a moment, gets FFmpeg's own client), a refused
connect fails the load at once naming the host (and telling a sinkholed name
from a dead node), a URL the reader failed on goes to FFmpeg next time, the
backfill fetches behind a resume point on spare time only -- with three
changes the measurements asked for:

- A refused (429) run is handed back at once and its worker backs off
  holding nothing: a chunk never waits out a backoff in the hands of a
  worker that cannot fetch it.
- After a refusal the reader count drops straight to the number of requests
  the host is serving at that moment, which is what it accepts, rather than
  one per half second.
- The read-ahead window starts at one run and grows while the reader goes on
  in sequence; opening a file is a few scattered reads (header, index,
  resume point), and a full window at the first byte spent connections on
  stretches the demuxer was about to jump away from.
- While playback is paused the window is the whole cache ahead (2 GiB on
  disk): the demuxer stops at its 30 s of packets, and a pause right after
  a start or a seek would otherwise have loaded little more than that.
  The demuxer's loop, which keeps turning while its queues are full, tells
  the reader (`Source::set_paused`).

Against a local origin with 0.3 s per request, resuming a 246 MB file at
617 s: first frame in 1.30 s, against 1.50 s for the engine through the
proxy and 1.47 s for mpv through it; with the host allowing one connection,
2.05 s (4 refusals) against 2.71 s (17) and 2.22-2.72 s (12-13). The app's
accelerator setting turns it on and off (`EngineStreamReading` stands in for
the proxy), and the rate the connection estimate samples is the engine's own
network rate, never the demuxer's rate off the local cache.

**A resume starts at the keyframe before the point.** `LoadOptions::
keyframe_start` (the app sets it for every start past zero: a resume, or a
reload after a stall) looks the start up in the demuxer's index and, when a
keyframe lies at most `MAX_KEYFRAME_LEAD_S` (10 s) before it, moves the
serial's target and the clock there (`Core::retarget`). Starting exactly
means decoding from that keyframe to the point and throwing every frame
away, which over the network is also reading those bytes first. Measured
against a local origin at 0.25 s a request and 100 Mbit/s, a 4K HEVC file
resumed at 63.5 s: first frame in 1.29 s against 1.46 s with a keyframe
every second, and in 1.27 s against 2.09 s (6 requests against 13) with
one every four. A file whose keyframes are further apart than the cap
starts exactly, rather than replaying half a scene.

**A file's opening reads are kept between sessions** (`src/net/keep.rs`).
Opening a Matroska file is a header at the start, an index at the end and
back again, each a round trip, and resuming the same file tomorrow reads
exactly the same bytes through a new signed URL that says nothing about
being the same file. So the chunks the demuxer read before the file was
open (`Source::mark_opened`, called once the start position is reached)
are kept in `NetworkSettings::keep_dir`, keyed by the file's size and an
FNV-1a of its first 64 KiB -- which the reader has one round trip in -- and
put in the cache before the demuxer is handed those first bytes, which are
held back until all 64 KiB are in. That ordering matters: a Matroska
header is in the first few KiB, and handed over as they arrived, the
demuxer had jumped to the index and asked for it before the key was
complete (measured: one warm open in two or three).
At most `MAX_FILE_BYTES` per file and `MAX_TOTAL_BYTES` in all, least
recently used dropped first; a disk that fails costs the head start, never
the stream. Unlike the chunk cache these bytes outlive the session, which
is a deliberate exception to "debrid bytes do not outlive their playback":
they are a header and an index, not a scene. Same setup as above: 1.27 s
cold, 0.97 s warm (one round trip).

**Zero-copy is libplacebo on Qt's device, with the Qt side in C++.** The
item asks `native/linux/gravitas_native_vk.cpp` (built into the video
bridge library) for Qt's Vulkan handles and hands them to the engine
(`attach_vulkan` → `pl_vulkan_import`). Frames render into a ring of three
RGBA8 images, each handed to Qt with `pl_vulkan_hold_ex` and taken back with
`pl_vulkan_release_ex` before it is drawn into again; both sides use the same
queue on Qt's render thread, so submission order plus libplacebo's barriers
order the work, and the hold's semaphore is a timeline one nobody has to wait
on. Subtitles are rasterised into a transparent picture and laid over as one
`pl_overlay`, re-uploaded only when they change. Three rules came out of
building it:

- The device libplacebo is told about must be the device Qt created: Qt
  enables every supported feature through the Vulkan 1.3 struct and none of
  1.4's, so the bridge reports API 1.3 at most. Reporting 1.4 had libplacebo
  use push descriptors Qt never enabled (the validation layer caught it).
- Teardown is C++ calling straight into the engine on the render thread, when
  Qt emits `sceneGraphInvalidated` and before it destroys the device. No
  Python runs there: the GUI thread may hold the GIL while it waits for the
  render thread. For the same reason engine logs reach Python from a thread of
  their own (`crates/python/src/logging.rs`), never from the thread that logs.
- Images a new size replaces are retired, not destroyed: Qt's renderer may
  sample a texture for frames after it last saw it. They go with the
  renderer, at teardown.

Checked under the Khronos validation layer with synchronisation validation:
nothing between libplacebo and Qt. What it does report is inside
libplacebo's upload of software-decoded frames (`vkCmdUpdateBuffer` then a
copy, with a barrier that does not cover that stage), on its own device as
much as on Qt's; frames decoded into the shared device (2d) never take it.

**Hardware decoding, copied back.** FFmpeg decodes on the GPU where it
can (`hwdec.rs`): NVDEC through CUDA, then VA-API, then Vulkan Video on
Linux; VideoToolbox on macOS; D3D11VA, then CUDA, then Vulkan on Windows.
Each device is created once per player, on a thread of its own as the first
file starts opening (CUDA's takes ~150 ms, which the header read now hides),
and a device that cannot be created is not tried again. Every decoded surface is
transferred to memory with its timestamps, colour tags and side data (Dolby
Vision's RPU included) and goes on exactly as a software frame would, so
nothing after the decoder knows the difference. A stream the hardware cannot
take -- H.264 High 10 on every decoder here, AV1 on older GPUs -- is declined
in FFmpeg's `get_format`, and the same decoder carries on in software.
`GRAVITAS_HWDEC` (the variable the mpv engine reads, in mpv's names) picks
one device or turns it off; `Player::hardware_decoder` reports what the
frames actually came from, as mpv's `hwdec-current` does. Two findings:

- The decoded pixels are identical: NVDEC's frames match libavcodec's byte
  for byte (checked with the ffmpeg CLI). They arrive as NV12/P010 rather
  than planar, which swscale converts along a different path (mean
  difference 1.6/255 against the planar frame); through libplacebo the two
  differ by at most 1.
- VA-API through nvidia-vaapi-driver can hang inside `vaSyncSurface` after a
  seek, and `ffmpeg -hwaccel vaapi -ss 1` hangs the same way on the same
  file. On NVIDIA the automatic order reaches CUDA first, as mpv's does.

**Decoding into the scene graph's own device (2d).** Qt's device has a
graphics queue and nothing else, and a decode queue has to exist from device
creation, so on Linux with Qt on Vulkan the engine makes the device
(`SharedDevice`, `crates/libplacebo-sys/src/shared_device.c`) on a
`QVulkanInstance` the Qt bridge creates, and the window adopts it before its
scene graph starts (`gv_native_vk_instance`, `gv_native_vk_adopt`, wired in
`main.py`). FFmpeg then decodes into Vulkan images on that device, the frames
stay there through the queue, and libplacebo samples them where they lie --
no copy anywhere. Four rules came out of building it:

- The device promises Qt what Qt's own would have: QRhi takes an adopted
  device's caps from the physical device and assumes its optional
  extensions, so every supported feature but robustness is enabled, plus
  Qt's extension list.
- FFmpeg decides by extension, not by feature: given
  `VK_KHR_video_maintenance1/2` with the features off, it used them anyway
  (validation caught it). So the device enables an explicit decode list,
  each with its feature struct, never FFmpeg's whole optional list.
- Qt submits to its graphics queue without libplacebo's queue lock, so the
  decoder must never touch that queue. FFmpeg is given the decode, compute
  and transfer families under libplacebo's locks, and the graphics family
  with nothing but `VK_QUEUE_GRAPHICS_BIT` -- listed so its images are
  shared with it (`VK_SHARING_MODE_CONCURRENT`), never chosen. A GPU with no
  compute queue apart from the graphics one gets no decoding into the
  device, and copies back as before.
- The device and its instance are never destroyed: Qt holds them until its
  window goes, at a moment nothing here chooses. The renderer that draws on
  it waits for its own work (`pl_gpu_finish`) before freeing its handover
  semaphore; freeing it mid-flight was a validation error.

Anything that cannot sample the shared device -- swscale, the readback
renderer on a device of its own, a window that did not adopt it -- copies
the shown frame out (`hwdec::copy_to_memory`), so every path still works.
Under the validation layer with synchronisation checks the path is clean,
with two exceptions that are not ours: `VUID-VkImageCreateInfo-pNext-06811`
on FFmpeg's decode images, which FFmpeg's own Vulkan device produces too
(`ffmpeg -init_hw_device vulkan -hwaccel vulkan`), and intermittent
`THREADING ERROR`s on the decode queue naming FFmpeg's frame threads. Those
threads all submit under libplacebo's queue lock; instrumenting the lock
with an owner check found no overlap in runs where the layer reported ten.

**On macOS, Metal renders, and nothing goes through Vulkan** (`src/metal/`).
libplacebo has no Metal backend and Qt's scene graph runs on Metal there;
MoltenVK was ruled out as a translation layer. So the engine has a renderer
of its own: one fragment shader (`shaders.metal`) samples the frame's planes,
applies Dolby Vision reshaping from the RPU FFmpeg parses (ETSI GS CCM 001:
polynomial and MMR pieces, then the RPU's matrices and a fixed
HPE-LMS-to-BT.2020 step derived in code from the published matrices),
linearises, and encodes for an sRGB display; every parameter is worked out
on the CPU (`colour.rs`), and the variants (plane layout, transfer, Dolby
Vision, tone mapping, subtitles) are function constants. It is written from
the standards and its own code only: libplacebo is LGPL, the engine MIT, so
nothing of libplacebo's own design is translated. Four rules came out of it:

- **SDR is the same arithmetic as libplacebo's**, so it matches: stripes of
  BT.709 colours and greys agree within 0.1 of an 8-bit step
  (`metal::tests::sdr_colours_match_libplacebo`, libplacebo through MoltenVK
  as the reference). BT.1886 sources keep the 1000:1 black the output
  subtracts again, as libplacebo does.
- **HDR is BT.2390's EETF on the intensity of BT.2100 ICtCp**, with BT.2390's
  chroma adjustment, onto SDR white at 203 nits (BT.2408). The source peak is
  measured from each frame on the GPU and never read back: a compute pass
  takes a histogram of PQ luminance, a one-thread pass reads the peak off it
  (all but one pixel in ten thousand below it), smooths it (up within a few
  frames, down over about a second, a jump of a tenth of PQ is a cut) and
  writes the curve the draw reads -- all in one command buffer. Dolby
  Vision's level 1 metadata replaces the measurement where present. Mid-tones
  come out brighter than libplacebo's spline makes them (113 nits: 191 here
  against 119), by design: below BT.2390's knee a nit is shown as a nit.
- **Out-of-gamut colours keep their saturation.** Each component's distance
  from the largest is compressed smoothly from 90% on (the idea of the
  Academy's reference gamut compression), and a colour still over white is
  scaled down whole. Two luminance-preserving versions turned bright BT.2020
  reds pink and greens cyan first.
- **Ordering against Qt needs no fence.** The zero-copy renderer submits to
  Qt's own command queue, on Qt's render thread; Metal runs a queue's command
  buffers in order and its hazard tracking orders a write after an earlier
  read of the same texture. Nothing is torn down with the scene graph either:
  the renderer holds its own references to Qt's device and queue, and the Qt
  bridge (`native/macos/gravitas_native_metal.mm`) holds one to every texture
  it wraps, because `QRhiTexture::createFrom` does not.

VideoToolbox's frames are `CVPixelBuffer`s on IOSurfaces, and the texture
cache wraps their planes as Metal textures on Qt's device: decoding,
rendering and the scene graph share the memory, the way 2d does on Linux.
Software frames are copied into textures the renderer keeps (one `memcpy` per
plane on unified memory); a format the shader has no layout for goes through
swscale first, keeping YUV as YUV.

**CoreAudio: the stream's latency, and the device's speakers.** cpal's
playback timestamp on macOS covers the device buffer, latency and safety
offset but not the output stream's latency -- 19.7 ms on a MacBook Air's
speakers, and where a Bluetooth sink reports its radio link -- so the mixer
adds it (`src/coreaudio.rs`), read again every second so a device change
takes its own. And a CoreAudio device states which speaker each channel is
(`kAudioDevicePropertyPreferredChannelLayout`, from Audio MIDI Setup): a 6- or
8-channel device whose every channel names a distinct 5.1/7.1 speaker gets
those channels in its own order (the mixer permutes FFmpeg's), anything else
plays stereo.

**Video is pulled, not pushed.** The QML item asks for the frame due at the
current clock on every scene-graph frame; the engine drops late frames there
and renders the chosen one straight into the item's buffer. Nothing is
converted that is not shown.

**Colour is libplacebo's job.** swscale converts matrices and ranges and
knows nothing of transfer functions or gamuts: HDR comes out washed out and
Dolby Vision profile 5 magenta. So frames render through libplacebo
(`gpu.rs`), which maps each FFmpeg frame with its colour tags, HDR metadata
and Dolby Vision RPU (`pl_map_avframe_ex`), tone-maps to SDR and scales. On
Linux with Qt on Vulkan it runs on **Qt's own device** and renders into images
the scene graph samples where they lie (zero-copy, below); elsewhere but
macOS it runs on a Vulkan device of its own and the picture is read back into
the item's buffer. On macOS the Metal renderer does all of this instead (see
"On macOS, Metal renders" above). That device comes up on a background thread when the
player is created, and swscale draws until it has -- and for good if there is
no usable Vulkan device (a software rasteriser is refused: it would be slower
than swscale) or a render fails. `GRAVITAS_NATIVE_RENDERER=software` forces
swscale. The Dolby Vision profile the port reports is 0 while libplacebo
renders, because the callers use it to warn about -- and remember -- releases
that play in the wrong colours, and through libplacebo profile 5 does not.

## Roadmap

1. **Engine core, software video** (done): demux/decode/sync, audio
   output, seek, pause, volume, audio/video track selection, chapters, the
   `MediaPlayer` adapter and a software-uploading video item.
2. **libplacebo**, in three steps:
   - **2a** (done): libplacebo on its own Vulkan device, read back into the
     software item. HDR tone mapping and Dolby Vision profile 5 come with it.
   - **2b** (done): zero-copy on Linux with Qt on Vulkan -- libplacebo on
     Qt's own device, rendering into images the scene graph samples.
   - **2c** (done): hardware decoding, each frame copied back to memory.
   - **2d** (done, Linux with Qt on Vulkan): decoding into the device Qt's
     scene graph renders on, which the engine makes and Qt adopts; frames
     never leave the GPU. Windows keeps 2c until its scene graph gets the
     same treatment.
   - **2e** (done, macOS): the Metal renderer, readback and zero-copy on
     Qt's Metal scene graph, with VideoToolbox's frames sampled where they
     were decoded.
3. **Subtitles** (done, ahead of 2 because the engine is not usable without
   them): text and ASS through libass with the file's fonts, image subtitles
   (PGS, VobSub) as overlays, subtitle files beside the video, delay, hiding
   and the viewer's style. Instant switching between subtitle tracks came
   with it.
4. **Instant audio switching** (done): every audio track's packets are
   kept, so a switch is a decoder swap at the playhead, never a re-read.
5. **Network and cache** (done): parallel ranged reads and the disk cache
   moved from the Python proxy into the engine, behind FFmpeg's custom I/O.
6. **Parity and default.** Closing the gaps below, then packaging (AppImage,
   Flatpak, dmg, Windows), then making it the default with mpv as the
   fallback. Closed so far: compiled shaders kept on disk between runs
   (`configure_shader_cache`: the first render 273 ms uncached, 12 ms
   warm), a dropped-frame count for the decoder-capacity verdicts
   (`Player::dropped_frames`, frames discarded as late), and audio through
   the PulseAudio host on Linux with 5.1/7.1 where the sink has them.

## Status

Milestones 1 to 5 are in place on Linux and macOS, and milestone 6 has begun (see below): the engine, the PyO3 module, the
`NativePlayer` adapter, the video item, subtitles, libplacebo rendering,
hardware decoding, instant audio switching and the engine's own network
reading, the default player wherever the module is built. Measured on an
RTX 2070 SUPER:

- **Decoding**, 4K HEVC 10-bit in a window: 6% of one core decoding into
  the shared device, 30% with NVDEC copy-back (26-35% for NVDEC, Vulkan
  Video and VA-API copy-back in a readback run), 78% in software. The
  render thread's p95 per frame drops from 5.3 ms to 0.23 ms with the
  upload gone, and the first frame comes ~0.2 s sooner (830 against
  1070 ms). Making the shared device costs ~0.9 s once at start-up.
- **Rendering**, 1080p: 0.8 ms (p95) per frame on the zero-copy path,
  against 2.6 ms median / 5.4 ms p95 with the readback and 0.8 ms through
  swscale; an HDR10 test pattern renders in its colours through libplacebo
  and washed out through swscale.
- **A real AIOStreams → TorBox file** (South Park S6E1, 1080p H.264, six
  AC3 tracks, two SubRip tracks), resuming at 4:50 with the silent output:
  every track listed with mpv's labels, video at the file's rate, a seek to
  10:00 settled in 2.2 s. An audio switch there took 2.1 s before
  milestone 4; it is now in place (not yet re-measured on that stream --
  through the proxy against a throttled local origin, no switch reported
  loading at all, where the old path stalled up to 0.8 s).

On macOS (Apple silicon, Qt on Metal), 4K HEVC 10-bit HDR10 in a 1080p
window, per new frame on the render thread:

- **Zero-copy with VideoToolbox's frames sampled in place**: 0.41 ms median,
  0.54 ms p95; 15% of one core for the whole process; 623 MB resident. 1080p
  H.264 costs the same 0.43 / 0.54 ms.
- **VideoToolbox copied back** (what the engine did before): 8.2 / 11.0 ms,
  34%, 1187 MB. Software decoding: 3.7 / 4.1 ms (the plane copy), 78%.
- **Readback** (a Metal device of the renderer's own, read into the item's
  buffer): 9.8 / 12.0 ms.
- Metal compiles the shader library in 2 ms (the system keeps compiled
  libraries), first frame 115 ms after a local load, the clock moving at
  170 ms. Four fullscreen toggles mid-playback keep the same bridge and
  drop only the frames due while macOS animates (~0.6 s each).
- The bundle (`packaging/gravitas.spec`) carries the engine with FFmpeg and
  libass beside it and loads nothing from Homebrew (checked with
  `DYLD_PRINT_LIBRARIES`); no Vulkan loader or MoltenVK is needed.

Known gaps, in the order they matter:

- **Not yet measured on a real debrid stream.** The network numbers above
  are against a local origin shaped like one; the TorBox account these were
  first measured on was throttling after the earlier tests.
- **Software-decoded frames on macOS are copied into textures on the render
  thread**: 3.7 ms per 4K 10-bit frame, measured (hardware-decoded frames
  cost nothing there). Only codecs VideoToolbox lacks take that path (AV1
  before M3, H.264 10-bit, MPEG-2).
- **Not yet seen on macOS**: a real Dolby Vision profile 5 file (the RPU
  path is checked against synthetic metadata: an identity RPU reproduces the
  HDR10 picture, and polynomial and MMR curves the same reshaping done on
  the CPU), an HDR display, a multichannel output, and lip sync over
  Bluetooth.
- **Copy-back on Windows, and for codecs Vulkan Video lacks**: there
  every hardware frame travels GPU → memory → GPU (~24 MB a frame each way
  at 4K). MPEG-2, VC-1 and MPEG-4 part 2 go to NVDEC/VA-API copy-back even
  on Linux.
- **The shared device is made at start-up** (~0.7 s, whether or not
  anything plays). It is made on a thread of its own while the QML loads,
  so the window waits ~0.5 s of it, not all of it.
- **5.1 and 7.1 only through PulseAudio and CoreAudio**: both name each
  speaker (PulseAudio's channel map, CoreAudio's preferred channel layout),
  so a 6- or 8-channel sink gets the source's channels in place. WASAPI and
  ALSA without a sound server stay stereo until their channel order is
  checked. Not yet tried on a real 5.1 sink on either.
- **Lip sync on high-latency sinks** (Bluetooth) rests on the latency the
  PulseAudio host reports in its playback timestamps; not yet checked by eye.
