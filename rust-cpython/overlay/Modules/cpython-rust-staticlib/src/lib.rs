pub use _asyncio_rs::PyInit__asyncio_rs;
pub use _base64::PyInit__base64;
pub use _bisect_rs::PyInit__bisect_rs;
pub use _codecs_rs::PyInit__codecs_rs;
pub use _concurrent_futures_rs::PyInit__concurrent_futures_rs;
pub use _contextlib_rs::PyInit__contextlib_rs;
pub use _heapq_rs::PyInit__heapq_rs;
pub use _html_parser_rs::PyInit__html_parser_rs;
pub use _itertools_rs::PyInit__itertools_rs;
pub use _jit_opt_rs::_PyJitOpt_PlanUnboxing;
pub use _jit_opt_rs::_PyJitOpt_PlanLeafCalls;
pub use _jit_opt_rs::_PyJitOpt_PlanCache;
pub use _jit_opt_rs::_PyJitOpt_PlanLoopGlobals;
pub use _jit_opt_rs::_PyJitOpt_PlanStores;
pub use _jit_opt_rs::_PyJitOpt_PlanLoopTypes;
pub use _posixpath_rs::PyInit__posixpath_rs;
pub use _random_rs::PyInit__random_rs;
pub use _tempfile_rs::PyInit__tempfile_rs;
#[cfg(feature = "serc")]
pub use serc_lane::{_PySerc_CompileSource, _PySerc_DecodesAsUtf8, _PySerc_ReleaseBuffer};

mod io;

#[cfg(all(feature = "serc", target_os = "linux", not(test)))]
mod mimalloc {
    use core::alloc::{GlobalAlloc, Layout};
    use core::ffi::c_void;
    use core::sync::atomic::{AtomicU8, Ordering};

    const MI_OPTION_EAGER_COMMIT: i32 = 3;
    const MI_OPTION_PURGE_DELAY: i32 = 15;
    const MI_OPTION_ALLOW_THP: i32 = 37;

    unsafe extern "C" {
        fn mi_malloc_aligned(size: usize, alignment: usize) -> *mut c_void;
        fn mi_zalloc_aligned(size: usize, alignment: usize) -> *mut c_void;
        fn mi_realloc_aligned(pointer: *mut c_void, size: usize, alignment: usize) -> *mut c_void;
        fn mi_free(pointer: *mut c_void);
        fn mi_option_set(option: i32, value: core::ffi::c_long);
    }

    static CONFIGURE_STATE: AtomicU8 = AtomicU8::new(0);

    fn configure() {
        if CONFIGURE_STATE.load(Ordering::Acquire) == 2 {
            return;
        }
        if CONFIGURE_STATE
            .compare_exchange(0, 1, Ordering::AcqRel, Ordering::Acquire)
            .is_ok()
        {
            unsafe {
                mi_option_set(MI_OPTION_PURGE_DELAY, 0);
                mi_option_set(MI_OPTION_EAGER_COMMIT, 0);
                mi_option_set(MI_OPTION_ALLOW_THP, 0);
            }
            CONFIGURE_STATE.store(2, Ordering::Release);
        } else {
            while CONFIGURE_STATE.load(Ordering::Acquire) != 2 {
                core::hint::spin_loop();
            }
        }
    }

    /// Routes the staticlib's Rust allocations to libpython's mimalloc, which is
    /// process-wide and needs no attached thread state.
    pub struct Mimalloc;

    unsafe impl GlobalAlloc for Mimalloc {
        unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
            configure();
            unsafe { mi_malloc_aligned(layout.size(), layout.align()).cast() }
        }
        unsafe fn alloc_zeroed(&self, layout: Layout) -> *mut u8 {
            configure();
            unsafe { mi_zalloc_aligned(layout.size(), layout.align()).cast() }
        }
        unsafe fn dealloc(&self, pointer: *mut u8, _layout: Layout) {
            unsafe { mi_free(pointer.cast()) }
        }
        unsafe fn realloc(&self, pointer: *mut u8, layout: Layout, size: usize) -> *mut u8 {
            unsafe { mi_realloc_aligned(pointer.cast(), size, layout.align()).cast() }
        }
    }

    #[global_allocator]
    static ALLOCATOR: Mimalloc = Mimalloc;
}
