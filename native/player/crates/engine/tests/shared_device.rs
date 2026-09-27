//! Decoding into the shared Vulkan device, end to end and without Qt: the
//! test creates the instance the embedder would, attaches the renderer the
//! way the embedder's scene graph does, and reads the zero-copy picture back.
//!
//! Skipped when the `ffmpeg` CLI, a Vulkan device, or a video decode queue
//! is missing. Not built on macOS, which renders through Metal (see
//! `tests/metal.rs`).

#![cfg(not(target_os = "macos"))]

use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::{Arc, OnceLock};
use std::time::{Duration, Instant};

use gravitas_player_engine::{
    AudioBackend, Event, HardwareDecoding, LoadOptions, Player, RenderTarget, SharedDevice,
};
use libplacebo_sys as pl;
use parking_lot::Mutex;

const WIDTH: u32 = 320;
const HEIGHT: u32 = 180;

/// An H.264 test pattern in 4:2:0, which every Vulkan Video decoder takes.
fn sample() -> Option<&'static Path> {
    static SAMPLE: OnceLock<Option<PathBuf>> = OnceLock::new();
    SAMPLE
        .get_or_init(|| {
            let dir = std::env::temp_dir().join(format!(
                "gravitas-shared-device-test-{}",
                std::process::id()
            ));
            std::fs::create_dir_all(&dir).ok()?;
            let path = dir.join("h264.mkv");
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

/// The shared device, on an instance of libplacebo's making (the app makes
/// it with Qt). Both live for the process, as they do in the app.
fn shared() -> Option<&'static SharedDevice> {
    static DEVICE: OnceLock<Option<&'static SharedDevice>> = OnceLock::new();
    *DEVICE.get_or_init(|| {
        let params = pl::pl_vk_inst_params::default();
        // SAFETY: default parameters; a null log drops libplacebo's messages.
        let instance = unsafe { pl::pl_vk_inst_create(std::ptr::null(), &raw const params) };
        if instance.is_null() {
            return None;
        }
        // SAFETY: a live instance, never destroyed.
        let (handle, get_proc_addr) = unsafe {
            (
                (*instance).instance as u64,
                (*instance).get_proc_addr.map_or(0, |f| f as usize as u64),
            )
        };
        // SAFETY: a live instance at the loader's highest version, never
        // destroyed.
        unsafe { SharedDevice::create(handle, get_proc_addr) }.ok()
    })
}

/// The picture at 1s, decoded under `mode`, rendered on the shared device
/// the way the embedder's scene graph would get it, and the hardware decoder
/// that produced it.
fn zero_copy_picture(
    sample: &Path,
    device: &'static SharedDevice,
    mode: HardwareDecoding,
) -> (Vec<u8>, Option<String>) {
    let events = Arc::new(Mutex::new(Vec::new()));
    let sink = {
        let events = events.clone();
        move |event: Event| events.lock().push(event)
    };
    let player = Player::new(AudioBackend::Null, sink).expect("the player starts");
    player.set_hardware_decoding(mode);
    player.use_shared_device(device);
    // SAFETY: the device is live for the process, and this thread is the
    // only one submitting to its queue.
    let teardown = unsafe { player.attach_vulkan(&device.device()) }.expect("libplacebo attaches");
    player.set_paused(true);
    player
        .load(LoadOptions {
            url: sample.to_str().unwrap().to_owned(),
            start: 1.0,
            ..LoadOptions::default()
        })
        .expect("load starts");
    let deadline = Instant::now() + Duration::from_secs(10);
    let first_frame = || {
        events
            .lock()
            .iter()
            .any(|e| matches!(e, Event::FirstFrame { .. }))
    };
    while !first_frame() {
        assert!(
            Instant::now() < deadline,
            "no first frame: {:?}",
            events.lock()
        );
        player
            .render_shared(false)
            .expect("renders on the shared device");
        std::thread::sleep(Duration::from_millis(5));
    }
    let image = player
        .render_shared(true)
        .expect("renders on the shared device")
        .expect("an image");
    assert_eq!((image.width, image.height), (WIDTH, HEIGHT));
    let mut pixels = vec![0; (WIDTH * HEIGHT * 4) as usize];
    player
        .read_shared(&mut RenderTarget {
            pixels: &mut pixels,
            width: WIDTH,
            height: HEIGHT,
            stride: WIDTH as usize * 4,
        })
        .expect("the picture reads back");
    let decoder = player.hardware_decoder();
    // SAFETY: the embedder's teardown, on the thread that rendered.
    unsafe { (teardown.callback)(teardown.context) };
    (pixels, decoder)
}

fn largest_difference(a: &[u8], b: &[u8]) -> u8 {
    a.iter()
        .zip(b)
        .map(|(x, y)| x.abs_diff(*y))
        .max()
        .unwrap_or(0)
}

/// Frames decoded into the shared device are sampled where the decoder
/// wrote them, and come out as the same picture software decoding gives.
#[test]
fn frames_decoded_into_the_shared_device_are_sampled_in_place() {
    let Some(sample) = sample() else {
        eprintln!("skipped: the ffmpeg CLI is not available to make a sample");
        return;
    };
    let Some(device) = shared().filter(|d| d.decodes()) else {
        eprintln!("skipped: no Vulkan device with a video decode queue");
        return;
    };
    let (in_place, decoder) = zero_copy_picture(sample, device, HardwareDecoding::Auto);
    assert_eq!(decoder.as_deref(), Some("vulkan"));
    let (software, decoder) = zero_copy_picture(sample, device, HardwareDecoding::Off);
    assert_eq!(decoder, None);
    assert!(in_place.iter().any(|&b| b != 0), "the picture was drawn");
    let largest = largest_difference(&in_place, &software);
    assert!(largest <= 2, "pixels differ by up to {largest}");
}

/// A player whose frames are in the shared device but which renders
/// elsewhere (the readback path, or swscale) copies each shown frame out.
#[test]
fn frames_in_the_shared_device_still_render_elsewhere() {
    let Some(sample) = sample() else {
        return;
    };
    let Some(device) = shared().filter(|d| d.decodes()) else {
        return;
    };
    let events = Arc::new(Mutex::new(Vec::new()));
    let sink = {
        let events = events.clone();
        move |event: Event| events.lock().push(event)
    };
    let player = Player::new(AudioBackend::Null, sink).expect("the player starts");
    player.set_gpu_rendering(false);
    player.use_shared_device(device);
    player.set_paused(true);
    player
        .load(LoadOptions {
            url: sample.to_str().unwrap().to_owned(),
            start: 1.0,
            ..LoadOptions::default()
        })
        .expect("load starts");
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
            .expect("renders through swscale");
        if drawn {
            break;
        }
        assert!(
            Instant::now() < deadline,
            "nothing drawn: {:?}",
            events.lock()
        );
        std::thread::sleep(Duration::from_millis(5));
    }
    assert_eq!(player.hardware_decoder().as_deref(), Some("vulkan"));
    assert!(pixels.iter().any(|&b| b != 0), "the picture was drawn");
}
