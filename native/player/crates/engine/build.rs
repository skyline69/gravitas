//! On macOS, compiles the C that reads a frame's colour metadata for the
//! Metal renderer (see `src/metal/frame_colour.h`) against the installed
//! FFmpeg headers. Nothing to build elsewhere: libplacebo reads the same
//! metadata itself.

fn main() {
    // ffmpeg-next swaps its whole ChannelLayout type at FFmpeg 7 (a struct
    // over AVChannelLayout, a bitflags mask before it), on the version
    // ffmpeg-sys-next found. The same signal decides which one decode.rs
    // builds against, so the two cannot disagree.
    println!("cargo::rustc-check-cfg=cfg(ffmpeg_7)");
    if std::env::var("DEP_FFMPEG_FFMPEG_7_0").as_deref() == Ok("true") {
        println!("cargo:rustc-cfg=ffmpeg_7");
    }
    println!("cargo:rerun-if-changed=src/metal/frame_colour.c");
    println!("cargo:rerun-if-changed=src/metal/frame_colour.h");
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() != Ok("macos") {
        return;
    }
    let avutil = pkg_config::Config::new()
        .cargo_metadata(false)
        .probe("libavutil")
        .expect("FFmpeg development files (brew install ffmpeg)");
    let mut build = cc::Build::new();
    build.file("src/metal/frame_colour.c");
    for path in &avutil.include_paths {
        build.include(path);
    }
    build.compile("gravitas_frame_colour");
}
