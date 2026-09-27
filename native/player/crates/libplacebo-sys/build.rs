//! Finds libplacebo (and the FFmpeg headers its libav helpers need) through
//! pkg-config, compiles those helpers (see src/libav.c), and generates
//! bindings for the parts of the API the engine uses.

use std::env;
use std::path::PathBuf;

fn main() {
    let placebo = pkg_config::Config::new()
        .atleast_version("6.338")
        .probe("libplacebo")
        .expect("libplacebo development files (libplacebo-devel / libplacebo-dev / brew install libplacebo)");
    let mut include_paths = placebo.include_paths.clone();
    for library in ["libavutil", "libavcodec", "libavformat"] {
        let found = pkg_config::Config::new()
            .cargo_metadata(false)
            .probe(library)
            .expect("FFmpeg development files");
        include_paths.extend(found.include_paths);
    }

    let mut helpers = cc::Build::new();
    helpers
        .file("src/libav.c")
        .file("src/shared_device.c")
        .warnings(false);
    for path in &include_paths {
        helpers.include(path);
    }
    helpers.compile("gravitas_placebo_libav");
    for source in ["src/libav.c", "src/shared_device.c", "src/shared_device.h"] {
        println!("cargo:rerun-if-changed={source}");
    }

    let mut builder = bindgen::Builder::default()
        // The helpers are real functions here (see src/libav.c), so they are
        // declared, not inlined.
        .header_contents(
            "wrapper.h",
            "#define PL_LIBAV_API\n#define PL_LIBAV_IMPLEMENTATION 0\n\
             #include <libplacebo/vulkan.h>\n#include <libplacebo/renderer.h>\n#include <libplacebo/cache.h>\n\
             #include <libplacebo/utils/libav.h>\n#include \"src/shared_device.h\"\n",
        )
        // What the engine uses, and what that reaches: the full headers
        // also bind the Dolby Vision and stream-side-data helpers, whose
        // FFmpeg types ffmpeg-sys-next does not generate.
        .allowlist_function(
            "pl_(log|vulkan|renderer|render|tex|gpu|frame|map_avframe|unmap_avframe|find|plane|color|raw|version|cache).*|pl_vk_inst.*|gv_shared_device_.*",
        )
        .blocklist_function("pl_(map_avdovi|frame_copy_stream_props|frame_map_avdovi|av_hdr|map_hdr_metadata).*")
        .blocklist_type("pl_av_hdr_metadata")
        .allowlist_type("pl_.*")
        .allowlist_var("pl_.*|PL_.*")
        // FFmpeg's types come from ffmpeg-sys-next, so a frame decoded there
        // can be handed to libplacebo without a cast between two layouts.
        .blocklist_type("AV.*")
        .raw_line("#[allow(unused_imports)]\nuse ffmpeg_sys_next::*;")
        .default_enum_style(bindgen::EnumVariation::Consts)
        .prepend_enum_name(false)
        .derive_default(true)
        .layout_tests(false)
        .parse_callbacks(Box::new(bindgen::CargoCallbacks::new()));
    for path in &include_paths {
        builder = builder.clang_arg(format!("-I{}", path.display()));
    }
    // `pl_log_create` is a macro naming a symbol after the API version
    // (pl_log_create_360), so a build against another libplacebo links a
    // differently named function. The alias keeps callers version-blind; the
    // API version is the library's minor version.
    let api_version = placebo
        .version
        .split('.')
        .nth(1)
        .expect("libplacebo's version names its API version")
        .to_owned();
    builder = builder.raw_line(format!(
        "pub use self::pl_log_create_{api_version} as pl_log_create;"
    ));
    let bindings = builder.generate().expect("libplacebo bindings");
    let out = PathBuf::from(env::var("OUT_DIR").expect("cargo sets OUT_DIR"));
    bindings
        .write_to_file(out.join("bindings.rs"))
        .expect("bindings written");
}
