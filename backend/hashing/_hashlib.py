import _blake2
import _md5
import _operator
import _sha1
import _sha2
import _sha3
import inspect
import warnings

import _hashlib_graviola

_NO_DATA = object()
_openssl_fallback_signature = inspect.Signature(
    (
        inspect.Parameter("data", inspect.Parameter.POSITIONAL_OR_KEYWORD, default=b""),
        inspect.Parameter("usedforsecurity", inspect.Parameter.KEYWORD_ONLY, default=True),
        inspect.Parameter("string", inspect.Parameter.KEYWORD_ONLY, default=None),
    )
)


def _md5_sha1(data=b"", *, usedforsecurity=True):
    return _MD5SHA1(data, usedforsecurity=usedforsecurity)


class _MD5SHA1:
    name = "md5-sha1"
    digest_size = 36
    block_size = 64
    __slots__ = ("_md5", "_sha1")

    def __init__(self, data=b"", *, usedforsecurity=True):
        usedforsecurity = bool(usedforsecurity)
        self._md5 = _md5.md5(data, usedforsecurity=usedforsecurity)
        self._sha1 = _sha1.sha1(data, usedforsecurity=usedforsecurity)

    def update(self, data):
        self._md5.update(data)
        self._sha1.update(data)

    def digest(self):
        return self._md5.digest() + self._sha1.digest()

    def hexdigest(self):
        return self._md5.hexdigest() + self._sha1.hexdigest()

    def copy(self):
        result = object.__new__(type(self))
        result._md5 = self._md5.copy()
        result._sha1 = self._sha1.copy()
        return result


def _rust_constructor(algorithm):
    class RustHash:
        __slots__ = ("_block_size", "_digest_size", "_name", "_state")

        def __init__(self, data=b"", *, usedforsecurity=True):
            bool(usedforsecurity)
            self._state, self._digest_size, self._block_size = (
                _hashlib_graviola.hash_state_new(algorithm, data)
            )
            self._name = algorithm

        @property
        def name(self):
            return self._name

        @property
        def digest_size(self):
            return self._digest_size

        @property
        def block_size(self):
            return self._block_size

        def __setattr__(self, name, value):
            if name in ("name", "digest_size", "block_size"):
                raise AttributeError(
                    f"attribute '{name}' of '_hashlib.HASH' objects is not writable"
                )
            object.__setattr__(self, name, value)

        def __delattr__(self, name):
            if name in ("name", "digest_size", "block_size"):
                raise AttributeError(
                    f"attribute '{name}' of '_hashlib.HASH' objects is not writable"
                )
            object.__delattr__(self, name)

        def update(self, data):
            _hashlib_graviola.state_update(self._state, data)

        def digest(self):
            return _hashlib_graviola.state_digest(self._state)

        def hexdigest(self):
            return self.digest().hex()

        def __reduce_ex__(self, protocol):
            raise TypeError("cannot pickle '_hashlib.HASH' object")

        def copy(self):
            result = object.__new__(type(self))
            result._state = _hashlib_graviola.state_copy(self._state)
            result._name = self._name
            result._digest_size = self._digest_size
            result._block_size = self._block_size
            return result

    def constructor(data=_NO_DATA, *, usedforsecurity=True, string=None):
        if string is not None:
            if data is not _NO_DATA:
                raise TypeError(
                    "'data' and 'string' are mutually exclusive and support for "
                    "'string' keyword parameter is slated for removal in a future version."
                )
            warnings.warn(
                "the 'string' keyword parameter is deprecated since Python 3.15 "
                "and slated for removal in Python 3.19; use the 'data' keyword "
                "parameter or pass the data to hash as a positional argument instead",
                DeprecationWarning,
                stacklevel=2,
            )
            data = string
        elif data is _NO_DATA:
            data = b""
        return RustHash(data, usedforsecurity=usedforsecurity)

    constructor.__signature__ = _openssl_fallback_signature
    constructor.__name__ = "openssl_" + algorithm
    constructor.__qualname__ = constructor.__name__
    constructor.__module__ = __name__
    return constructor


_constructors = {
    "md5": _md5.md5,
    "md5-sha1": _md5_sha1,
    "sha1": _sha1.sha1,
    "sha224": _sha2.sha224,
    "sha256": _sha2.sha256,
    "sha384": _sha2.sha384,
    "sha512": _sha2.sha512,
    "sha512_224": _rust_constructor("sha512_224"),
    "sha512_256": _rust_constructor("sha512_256"),
    "blake2b": _blake2.blake2b,
    "blake2s": _blake2.blake2s,
    "sha3_224": _sha3.sha3_224,
    "sha3_256": _sha3.sha3_256,
    "sha3_384": _sha3.sha3_384,
    "sha3_512": _sha3.sha3_512,
    "shake_128": _sha3.shake_128,
    "shake_256": _sha3.shake_256,
    "ripemd160": _rust_constructor("ripemd160"),
    "sm3": _rust_constructor("sm3"),
}
_MISSING = object()

openssl_md_meth_names = frozenset(_constructors)
algorithms_available = openssl_md_meth_names
_GIL_MINSIZE = 2048
UnsupportedDigestmodError = _hashlib_graviola.UnsupportedDigestmodError
compare_digest = _operator._compare_digest


class _HMACMeta(type):
    def __call__(cls, *args, **kwargs):
        raise TypeError("cannot create '_hashlib.HMAC' instances")

    def __setattr__(cls, name, value):
        raise TypeError("cannot set attributes of immutable type '_hashlib.HMAC'")

    def __delattr__(cls, name):
        raise TypeError("cannot delete attributes of immutable type '_hashlib.HMAC'")


class HMAC(metaclass=_HMACMeta):
    __slots__ = ("_algorithm", "_block_size", "_digest_size", "_state")

    def __new__(cls, *args, **kwargs):
        raise TypeError("cannot create '_hashlib.HMAC' instances")

    def __init__(self, key, msg=None, digestmod=None):
        if digestmod is None:
            raise TypeError("Missing required argument 'digestmod'.")
        algorithm = _digest_name(digestmod)
        self._state, self._digest_size, self._block_size = _hashlib_graviola.hmac_state_new(
            key, algorithm
        )
        self._algorithm = algorithm
        if msg is not None:
            _hashlib_graviola.state_update(self._state, msg)

    @property
    def name(self):
        return "hmac-" + self._algorithm

    @property
    def digest_size(self):
        return self._digest_size

    @property
    def block_size(self):
        return self._block_size

    def __setattr__(self, name, value):
        if name in ("name", "digest_size", "block_size"):
            raise AttributeError(
                f"attribute '{name}' of '_hashlib.HMAC' objects is not writable"
            )
        object.__setattr__(self, name, value)

    def __delattr__(self, name):
        if name in ("name", "digest_size", "block_size"):
            raise AttributeError(
                f"attribute '{name}' of '_hashlib.HMAC' objects is not writable"
            )
        object.__delattr__(self, name)

    def update(self, msg):
        _hashlib_graviola.state_update(self._state, msg)

    def digest(self):
        return _hashlib_graviola.state_digest(self._state)

    def hexdigest(self):
        return self.digest().hex()

    def __reduce_ex__(self, protocol):
        raise TypeError("cannot pickle '_hashlib.HMAC' object")

    def copy(self):
        result = object.__new__(type(self))
        result._state = _hashlib_graviola.state_copy(self._state)
        result._algorithm = self._algorithm
        result._digest_size = self._digest_size
        result._block_size = self._block_size
        return result

    def __repr__(self):
        return f"<{self._algorithm} HMAC object @ {id(self):#x}>"


def _digest_name(digestmod):
    if isinstance(digestmod, str):
        return _normalize_digest_name(digestmod)
    for name, constructor in _digestmod_constructors.items():
        if constructor is digestmod:
            return name
    raise UnsupportedDigestmodError(
        "[digital envelope routines] unsupported hash algorithm "
    )


def hmac_new(key, msg=b"", digestmod=_MISSING):
    if digestmod is _MISSING:
        raise TypeError("Missing required argument 'digestmod'.")
    result = object.__new__(HMAC)
    HMAC.__init__(result, key, msg, digestmod)
    return result


def hmac_digest(key, msg, digest):
    return _hashlib_graviola.hmac_digest(key, msg, _digest_name(digest))


def pbkdf2_hmac(hash_name, password, salt, iterations, dklen=None):
    return _hashlib_graviola.pbkdf2_hmac(
        hash_name, password, salt, iterations, dklen
    )


def scrypt(password, *, salt, n, r, p, maxmem=0, dklen=64):
    return _hashlib_graviola.scrypt(password, salt, n, r, p, maxmem, dklen)


def get_fips_mode():
    return 0


_openssl_names = (
    "md5",
    "sha1",
    "sha224",
    "sha256",
    "sha384",
    "sha512",
    "sha3_224",
    "sha3_256",
    "sha3_384",
    "sha3_512",
    "shake_128",
    "shake_256",
)


class _OpensslConstructor:
    """Non-descriptor callable: Python functions stored as class attributes
    bind as methods, which breaks callers such as setuptools that store
    hashlib.sha256 on a class and call it with the instance's argument."""

    def __init__(self, name: str):
        self._builtin = _constructors[name]
        try:
            self.__signature__ = inspect.signature(self._builtin)
        except (TypeError, ValueError):
            self.__signature__ = _openssl_fallback_signature
        self.__name__ = "openssl_" + name
        self.__qualname__ = self.__name__
        self.__module__ = __name__

    def __call__(self, *args, **kwargs):
        return self._builtin(*args, **kwargs)

    def __repr__(self):
        return f"<built-in function {self.__name__} (built-in)>"


for _name in _openssl_names:
    globals()["openssl_" + _name] = _OpensslConstructor(_name)

_digestmod_constructors = {name: globals()["openssl_" + name] for name in _openssl_names}


def new(name, *args, **kwargs):
    if len(args) > 1:
        raise TypeError(f"new() takes at most 2 positional arguments but {len(args) + 1} were given")
    if "data" in kwargs and args:
        raise TypeError("new() got multiple values for argument given by name ('data') and position")
    if not isinstance(name, str):
        raise TypeError(f"new() argument 'name' must be str, not {type(name).__name__}")
    normalized_name = _normalize_digest_name(name)
    try:
        constructor = _constructors[normalized_name]
    except KeyError:
        if "usedforsecurity" in kwargs:
            bool(kwargs["usedforsecurity"])
        raise UnsupportedDigestmodError(
            f"[digital envelope routines] unsupported hash algorithm {name}"
        ) from None
    if args:
        return constructor(args[0], **kwargs)
    if "data" in kwargs:
        data = kwargs.pop("data")
        return constructor(data, **kwargs)
    return constructor(**kwargs)


def _normalize_digest_name(name):
    normalized = name.lower()
    aliases = {
        "sha-1": "sha1",
        "sha-224": "sha224",
        "sha-256": "sha256",
        "sha-384": "sha384",
        "sha-512": "sha512",
        "sha512-224": "sha512_224",
        "sha512-256": "sha512_256",
        "blake2b512": "blake2b",
        "blake2b-512": "blake2b",
        "blake2s256": "blake2s",
        "blake2s-256": "blake2s",
        "sha3-224": "sha3_224",
        "sha3-256": "sha3_256",
        "sha3-384": "sha3_384",
        "sha3-512": "sha3_512",
        "shake-128": "shake_128",
        "shake-256": "shake_256",
        "ripemd-160": "ripemd160",
    }
    return aliases.get(normalized, normalized)
