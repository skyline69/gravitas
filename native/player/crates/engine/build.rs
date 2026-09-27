//! On macOS, compiles the C that reads a frame's colour metadata for the
//! Metal renderer (see `src/metal/frame_colour.h`) against the installed
//! FFmpeg headers. Nothing to build elsewhere: libplacebo reads the same
//! metadata itself.

fn main() {
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
