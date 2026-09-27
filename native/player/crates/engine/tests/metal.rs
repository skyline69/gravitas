//! Zero-copy video on macOS, end to end and without Qt: the test makes the
//! Metal device and command queue the embedder's scene graph would hand
//! over, attaches the renderer to them, and reads the picture back.
//!
//! Skipped when the `ffmpeg` CLI or a Metal device is missing.

#![cfg(target_os = "macos")]

use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::{Arc, OnceLock};
use std::time::{Duration, Instant};

use gravitas_player_engine::{
    AudioBackend, Event, HardwareDecoding, LoadOptions, MetalDevice, Player, RenderTarget,
};
use objc2::rc::Retained;
use objc2::runtime::ProtocolObject;
use objc2_metal::{MTLCommandQueue, MTLCreateSystemDefaultDevice, MTLDevice};
use parking_lot::Mutex;

const WIDTH: u32 = 640;
const HEIGHT: u32 = 360;

/// An H.264 test pattern in 4:2:0, which VideoToolbox decodes.
fn sample() -> Option<&'static Path> {
    static SAMPLE: OnceLock<Option<PathBuf>> = OnceLock::new();
    SAMPLE
        .get_or_init(|| {
            let dir =
                std::env::temp_dir().join(format!("gravitas-metal-test-{}", std::process::id()));
            std::fs::create_dir_all(&dir).ok()?;
            let path = dir.join("h264.mp4");
            let status = Command::new("ffmpeg")
                .args(["-loglevel", "error", "-y", "-f", "lavfi", "-i"])
                .arg(format!("testsrc2=size={WIDTH}x{HEIGHT}:rate=25:duration=3"))
                .args(["-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "10"])
                .arg(&path)
                .status()
                .ok()?;
            status.success().then_some(path)
        })
        .as_deref()
}

/// A device and queue standing in for Qt's, kept for the process as Qt's
/// are kept for its window.
fn qt_device() -> Option<MetalDevice> {
    struct Held(
        Retained<ProtocolObject<dyn MTLDevice>>,
        Retained<ProtocolObject<dyn MTLCommandQueue>>,
    );
    // SAFETY: Metal devices and queues are thread-safe objects.
    unsafe impl Send for Held {}
    unsafe impl Sync for Held {}
    static HELD: OnceLock<Option<Held>> = OnceLock::new();
    let held = HELD
        .get_or_init(|| {
            let device = MTLCreateSystemDefaultDevice()?;
            let queue = device.newCommandQueue()?;
            Some(Held(device, queue))
        })
        .as_ref()?;
    Some(MetalDevice {
        device: Retained::as_ptr(&held.0).cast::<std::ffi::c_void>() as u64,
        queue: Retained::as_ptr(&held.1).cast::<std::ffi::c_void>() as u64,
    })
}

fn player(events: &Arc<Mutex<Vec<Event>>>, mode: HardwareDecoding, sample: &Path) -> Player {
    let sink = {
        let events = events.clone();
        move |event: Event| events.lock().push(event)
    };
    let player = Player::new(AudioBackend::Null, sink).expect("the player starts");
    player.set_hardware_decoding(mode);
    player.set_paused(true);
    player
        .load(LoadOptions {
            url: sample.to_str().unwrap().to_owned(),
            start: 1.0,
            ..LoadOptions::default()
        })
        .expect("load starts");
    player
}

fn first_frame(events: &Mutex<Vec<Event>>) -> bool {
    events
        .lock()
        .iter()
        .any(|e| matches!(e, Event::FirstFrame { .. }))
}

/// The picture at 1s, decoded under `mode`, rendered into a texture on the
/// embedder's device, and the hardware decoder that produced it.
fn zero_copy_picture(sample: &Path, mode: HardwareDecoding) -> (Vec<u8>, Option<String>) {
    let events = Arc::new(Mutex::new(Vec::new()));
    let player = player(&events, mode, sample);
    let device = qt_device().expect("a Metal device");
    // SAFETY: the device and queue live for the process, and this thread is
    // the only one rendering.
    unsafe { player.attach_metal(&device) }.expect("the renderer attaches");
    let deadline = Instant::now() + Duration::from_secs(10);
    while !first_frame(&events) {
        assert!(
            Instant::now() < deadline,
            "no first frame: {:?}",
            events.lock()
        );
        player.render_shared(false).expect("renders on the device");
        std::thread::sleep(Duration::from_millis(5));
    }
    let image = player
        .render_shared(true)
        .expect("renders on the device")
        .expect("an image");
    assert_eq!((image.width, image.height), (WIDTH, HEIGHT));
    assert_ne!(image.image, 0, "a texture");
    let mut pixels = vec![0; (WIDTH * HEIGHT * 4) as usize];
    player
        .read_shared(&mut RenderTarget {
            pixels: &mut pixels,
            width: WIDTH,
            height: HEIGHT,
            stride: WIDTH as usize * 4,
        })
        .expect("the picture reads back");
    (pixels, player.hardware_decoder())
}

fn largest_difference(a: &[u8], b: &[u8]) -> u8 {
    a.iter()
        .zip(b)
        .map(|(x, y)| x.abs_diff(*y))
        .max()
        .unwrap_or(0)
}

/// VideoToolbox's frames are sampled where the decoder wrote them, and come
/// out as the same picture software decoding gives.
#[test]
fn videotoolbox_frames_are_sampled_in_place() {
    let Some(sample) = sample() else {
        eprintln!("skipped: the ffmpeg CLI is not available to make a sample");
        return;
    };
    if qt_device().is_none() {
        eprintln!("skipped: no Metal device");
        return;
    }
    let (in_place, decoder) = zero_copy_picture(sample, HardwareDecoding::Auto);
    assert_eq!(decoder.as_deref(), Some("videotoolbox"));
    let (software, decoder) = zero_copy_picture(sample, HardwareDecoding::Off);
    assert_eq!(decoder, None);
    assert!(in_place.iter().any(|&b| b != 0), "the picture was drawn");
    let largest = largest_difference(&in_place, &software);
    assert!(largest <= 2, "pixels differ by up to {largest}");
}

/// A VideoToolbox frame renders on every other path too: read back from a
/// Metal device of the renderer's own, and through swscale, which needs it
/// copied out of video memory first.
#[test]
fn videotoolbox_frames_render_on_every_path() {
    let Some(sample) = sample() else {
        return;
    };
    let mut pictures = Vec::new();
    for gpu in [true, false] {
        let events = Arc::new(Mutex::new(Vec::new()));
        let player = player(&events, HardwareDecoding::Auto, sample);
        player.set_gpu_rendering(gpu);
        if gpu {
            let deadline = Instant::now() + Duration::from_secs(10);
            while !player.renders_dolby_vision() {
                assert!(
                    Instant::now() < deadline,
                    "the Metal renderer never started"
                );
                std::thread::sleep(Duration::from_millis(5));
            }
        }
        let mut pixels = vec![0; (WIDTH * HEIGHT * 4) as usize];
        let deadline = Instant::now() + Duration::from_secs(10);
        loop {
            let drawn = player
                .render(&mut RenderTarget {
                    pixels: &mut pixels,
                    width: WIDTH,
                    height: HEIGHT,
                    stride: WIDTH as usize * 4,
                })
                .expect("renders");
            if drawn && first_frame(&events) {
                break;
            }
            assert!(
                Instant::now() < deadline,
                "nothing drawn: {:?}",
                events.lock()
            );
            std::thread::sleep(Duration::from_millis(5));
        }
        assert_eq!(player.hardware_decoder().as_deref(), Some("videotoolbox"));
        assert!(pixels.iter().any(|&b| b != 0), "the picture was drawn");
        pictures.push(pixels);
    }
    // swscale and the shader agree to within rounding and swscale's own
    // bilinear chroma (see README: the paths differ by ~1/255 on average).
    let mean = pictures[0]
        .iter()
        .zip(&pictures[1])
        .map(|(a, b)| f64::from(a.abs_diff(*b)))
        .sum::<f64>()
        / pictures[0].len() as f64;
    assert!(
        mean < 3.0,
        "Metal and swscale differ by {mean:.2} on average"
    );
}
