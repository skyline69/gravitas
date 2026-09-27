//! Finds libass through pkg-config and generates bindings for its public
//! header, so they always match the library the engine links against.

use std::env;
use std::path::PathBuf;

fn main() {
    let library = pkg_config::Config::new()
        .atleast_version("0.15")
        .probe("libass")
        .expect("libass development files (libass-devel / libass-dev / brew install libass)");
    let mut builder = bindgen::Builder::default()
        // The message callback's type, named: its `va_list` parameter is a
        // different Rust type on every platform, and a name is what lets
        // the engine write it once.
        .header_contents(
            "wrapper.h",
            "#include <stdarg.h>\n#include <ass/ass.h>\n\
             typedef void (*ass_sys_message_cb)(int level, const char *fmt, va_list args, void *data);\n",
        )
        .allowlist_function("ass_.*")
        .allowlist_type("ASS_.*|ass_sys_.*")
        .allowlist_var("ASS_.*|LIBASS_VERSION")
        .default_enum_style(bindgen::EnumVariation::Consts)
        .prepend_enum_name(false)
        .derive_default(true)
        .parse_callbacks(Box::new(bindgen::CargoCallbacks::new()));
    for path in &library.include_paths {
        builder = builder.clang_arg(format!("-I{}", path.display()));
    }
    let bindings = builder.generate().expect("libass bindings");
    let out = PathBuf::from(env::var("OUT_DIR").expect("cargo sets OUT_DIR"));
    bindings
        .write_to_file(out.join("bindings.rs"))
        .expect("bindings written");
}
