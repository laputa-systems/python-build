import collections
import importlib

from _ssl_metadata import _SSL_CONSTANTS

globals().update(_SSL_CONSTANTS)

_RUSTLS_BACKEND = True
_OpenSSLAPIVersionInfo = collections.namedtuple(
    "_OpenSSLAPIVersionInfo", ("major", "minor", "fix", "patch", "status")
)
OPENSSL_API_VERSION_INFO = _OpenSSLAPIVersionInfo(0, 0, 0, 0, 0)
_OPENSSL_API_VERSION = OPENSSL_API_VERSION_INFO
ENCODING_PEM = 1
ENCODING_DER = 2
PROTO_MINIMUM_SUPPORTED = -2
PROTO_MAXIMUM_SUPPORTED = -1
PROTO_SSLv3 = 0x0300
PROTO_TLSv1 = 0x0301
PROTO_TLSv1_1 = 0x0302
PROTO_TLSv1_2 = 0x0303
PROTO_TLSv1_3 = 0x0304

_KNOWN_OBJECTS = (
    (129, "serverAuth", "TLS Web Server Authentication", "1.3.6.1.5.5.7.3.1"),
    (130, "clientAuth", "TLS Web Client Authentication", "1.3.6.1.5.5.7.3.2"),
)


def _object_fields(oid):
    for nid, shortname, longname, known_oid in _KNOWN_OBJECTS:
        if oid == known_oid:
            return nid, shortname, longname, known_oid
    arcs = oid.split(".")
    if (
        len(arcs) >= 2
        and all(arc.isdigit() and arc for arc in arcs)
        and arcs[0] in ("0", "1", "2")
    ):
        return 0, None, None, oid
    raise ValueError(f"unknown object {oid!r}")


def txt2obj(txt, name=False):
    if isinstance(txt, str) and name:
        for nid, shortname, longname, oid in _KNOWN_OBJECTS:
            if txt in (shortname, longname, oid):
                return nid, shortname, longname, oid
        return _object_fields(txt)
    if not isinstance(txt, str):
        raise TypeError("txt must be str")
    return _object_fields(txt)


def nid2obj(nid):
    for fields in _KNOWN_OBJECTS:
        if nid == fields[0]:
            return fields
    raise ValueError(f"unknown NID {nid}")


def _test_decode_cert(path):
    import base64
    import re
    from rtls import _rustls

    try:
        with open(path, "rb") as source:
            pem = source.read()
    except OSError as error:
        raise __getattr__("SSLError")("Can't open file") from error
    match = re.search(
        rb"-----BEGIN CERTIFICATE-----\s*(.*?)\s*-----END CERTIFICATE-----",
        pem,
        re.DOTALL,
    )
    if match is None:
        raise __getattr__("SSLError")("Error decoding PEM-encoded file")
    try:
        der = base64.b64decode(b"".join(match[1].split()), validate=True)
        certificate = _rustls.parse_certificate_dict(der)
    except ValueError as error:
        raise __getattr__("SSLError")("Error decoding PEM-encoded file") from error
    return certificate


def __getattr__(name):
    if name == "Certificate":
        from rtls._certificate import TLSCertificate

        return TLSCertificate
    if name in ("_SSLContext", "_SSLSocket"):
        from rtls import _rustls  # noqa: F401

        if name == "_SSLContext":
            from rtls._context import TLSContext

            return TLSContext
        import ssl as _ssl_module

        return _ssl_module.SSLSocket
    if name in ("txt2obj", "nid2obj", "_test_decode_cert"):
        return globals()[name]
    if name.startswith("__"):
        raise AttributeError(name)
    module = importlib.import_module("ssl")
    aliases = {"_SSLContext": "SSLContext", "_SSLSocket": "SSLSocket"}
    try:
        return getattr(module, aliases.get(name, name))
    except AttributeError:
        raise AttributeError(f"module '_ssl' has no attribute {name!r}") from None
