"""Compression and decompression using zlib-compatible DEFLATE streams."""

import sys

# The frozen bootstrap modules are already loaded; using them directly keeps
# this wrapper from importing importlib, importlib.util, types, or os.path.
import _frozen_importlib as _bootstrap
import _frozen_importlib_external as _bootstrap_external


def _load_native_zlib():
    isfile = _bootstrap_external._path_isfile
    join = _bootstrap_external._path_join
    for directory in sys.path:
        if not isinstance(directory, str):
            continue
        if not directory:
            import os

            directory = os.getcwd()
        for suffix in _bootstrap_external.EXTENSION_SUFFIXES:
            path = join(directory, "zlib" + suffix)
            if not isfile(path):
                continue
            loader = _bootstrap_external.ExtensionFileLoader(__name__, path)
            spec = _bootstrap_external.spec_from_file_location(
                __name__, path, loader=loader
            )
            module = _bootstrap.module_from_spec(spec)
            loader.exec_module(module)
            return module
    raise ImportError("cannot locate the native zlib extension")


def _install_rust_codecs():
    native = _load_native_zlib()
    import _zlib_rs as rust

    native_compress = native.compress
    native_decompress = native.decompress
    native_compressobj = native.compressobj
    native_decompressobj = native.decompressobj
    omitted = object()

    def rust_call(function, *args):
        try:
            return function(*args)
        except ValueError as error:
            message = str(error)
            if message.startswith("Error "):
                raise native.error(message) from None
            raise

    class RustCompressor:
        __slots__ = ("_state",)

        def __new__(cls, *args, **kwargs):
            raise TypeError(
                f"cannot create '{cls.__module__}.{cls.__name__}' instances"
            )

        def compress(self, data, /):
            return rust_call(rust.compress, self._state, data)

        def flush(self, mode=native.Z_FINISH, /):
            return rust_call(rust.flush, self._state, mode)

        def copy(self):
            duplicate = object.__new__(RustCompressor)
            duplicate._state = rust.compressor_copy(self._state)
            return duplicate

        def __copy__(self):
            return self.copy()

        def __deepcopy__(self, memo):
            return self.copy()

    class RustDecompressor:
        __slots__ = ("_state",)

        def __new__(cls, *args, **kwargs):
            raise TypeError(
                f"cannot create '{cls.__module__}.{cls.__name__}' instances"
            )

        @property
        def eof(self):
            return rust.eof(self._state)

        @property
        def unused_data(self):
            return rust.unused_data(self._state)

        @property
        def unconsumed_tail(self):
            return rust.unconsumed_tail(self._state)

        def decompress(self, data, /, max_length=0):
            return rust_call(rust.decompress, self._state, data, max_length)

        def flush(self, length=native.DEF_BUF_SIZE, /):
            return rust_call(rust.decompressor_flush, self._state, length)

        def copy(self):
            duplicate = object.__new__(RustDecompressor)
            duplicate._state = rust.decompressor_copy(self._state)
            return duplicate

        def __copy__(self):
            return self.copy()

        def __deepcopy__(self, memo):
            return self.copy()

    RustCompressor.__name__ = "Compress"
    RustCompressor.__qualname__ = "Compress"
    RustCompressor.__module__ = __name__
    RustDecompressor.__name__ = "Decompress"
    RustDecompressor.__qualname__ = "Decompress"
    RustDecompressor.__module__ = __name__

    def new_compressor(level, wbits):
        compressor = object.__new__(RustCompressor)
        compressor._state = rust_call(rust.compressor, level, wbits)
        return compressor

    def new_decompressor(wbits):
        decompressor = object.__new__(RustDecompressor)
        decompressor._state = rust_call(rust.decompressor, wbits)
        return decompressor

    def compress(data, /, level=-1, wbits=native.MAX_WBITS):
        if (isinstance(level, int) and -1 <= level <= 9
                and isinstance(wbits, int)
                and wbits in (native.MAX_WBITS, -native.MAX_WBITS)):
            return rust_call(rust.compress_once, data, level, wbits)
        return native_compress(data, level, wbits)

    def decompress(data, /, wbits=native.MAX_WBITS,
                   bufsize=native.DEF_BUF_SIZE):
        if (isinstance(wbits, int)
                and wbits in (native.MAX_WBITS, -native.MAX_WBITS)
                and isinstance(bufsize, int)
                and 0 <= bufsize <= sys.maxsize):
            return rust_call(rust.decompress_once, data, wbits)
        return native_decompress(data, wbits, bufsize)

    def compressobj(level=-1, method=native.DEFLATED,
                    wbits=native.MAX_WBITS, memLevel=native.DEF_MEM_LEVEL,
                    strategy=native.Z_DEFAULT_STRATEGY, zdict=omitted):
        if (isinstance(level, int) and -1 <= level <= 9
                and isinstance(method, int)
                and method == native.DEFLATED
                and isinstance(wbits, int)
                and wbits in (native.MAX_WBITS, -native.MAX_WBITS)
                and isinstance(memLevel, int)
                and memLevel == native.DEF_MEM_LEVEL
                and isinstance(strategy, int)
                and strategy == native.Z_DEFAULT_STRATEGY
                and zdict is omitted):
            return new_compressor(level, wbits)
        if zdict is omitted:
            return native_compressobj(level, method, wbits, memLevel, strategy)
        return native_compressobj(level, method, wbits, memLevel, strategy,
                                  zdict)

    def decompressobj(wbits=native.MAX_WBITS, zdict=omitted):
        if (isinstance(wbits, int)
                and wbits in (native.MAX_WBITS, -native.MAX_WBITS)
                and zdict is omitted):
            return new_decompressor(wbits)
        if zdict is omitted:
            return native_decompressobj(wbits)
        return native_decompressobj(wbits, zdict)

    compress.__doc__ = native_compress.__doc__
    decompress.__doc__ = native_decompress.__doc__
    compressobj.__doc__ = native_compressobj.__doc__
    decompressobj.__doc__ = native_decompressobj.__doc__
    native.compress = compress
    native.decompress = decompress
    native.compressobj = compressobj
    native.decompressobj = decompressobj
    native._rust_decompressobj = decompressobj
    sys.modules[__name__] = native


_install_rust_codecs()
del _install_rust_codecs
del _load_native_zlib
