//! Link arguments for the module build.
//!
//! An extension module takes the interpreter's symbols from the process that
//! imports it, so it is linked without libpython. GNU ld accepts a shared
//! object with undefined symbols as it is; Apple's ld refuses one unless told
//! they are resolved at load time (`-undefined dynamic_lookup`), and failed
//! every `_Py*` symbol without it. PyO3's helper emits exactly that, on macOS
//! only, and for the cdylib only -- `cargo test` still links libpython.

fn main() {
    pyo3_build_config::add_extension_module_link_args();
}
