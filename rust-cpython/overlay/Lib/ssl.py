import os
import sys
from enum import IntEnum as _IntEnum, IntFlag as _IntFlag

from rtls import *
from rtls import __all__ as _rtls_all, __version__
from rtls import SSLContext as _RTLS_SSLContext

__all__ = list(_rtls_all)

socket_error = OSError


_PYTHON_BUILD_DEFAULT_CA_BUNDLE = os.path.join(
    os.path.dirname(__file__), "python-build-cacert.pem"
)
_PYTHON_BUILD_NO_DEFAULT_CA_BUNDLE = "PYTHON_BUILD_NO_DEFAULT_CA_BUNDLE"


class SSLContext(_RTLS_SSLContext):
    @property
    def protocol(self):
        return _SSLMethod(super().protocol)

    @property
    def verify_mode(self):
        return VerifyMode(super().verify_mode)

    @verify_mode.setter
    def verify_mode(self, value):
        _RTLS_SSLContext.verify_mode.fset(self, value)

    @property
    def options(self):
        return Options(super().options)

    @options.setter
    def options(self, value):
        _RTLS_SSLContext.options.fset(self, value)

    @property
    def verify_flags(self):
        return VerifyFlags(super().verify_flags)

    @verify_flags.setter
    def verify_flags(self, value):
        _RTLS_SSLContext.verify_flags.fset(self, value)

    def load_default_certs(self, purpose=Purpose.SERVER_AUTH):
        super().load_default_certs(purpose)
        default_paths = get_default_verify_paths()
        explicit_path = any(
            name in os.environ
            for name in (
                default_paths.openssl_cafile_env,
                default_paths.openssl_capath_env,
            )
        )
        if (
            not explicit_path
            and default_paths.cafile is None
            and default_paths.capath is None
            and os.environ.get(_PYTHON_BUILD_NO_DEFAULT_CA_BUNDLE) != "1"
            and os.path.isfile(_PYTHON_BUILD_DEFAULT_CA_BUNDLE)
        ):
            self.load_verify_locations(cafile=_PYTHON_BUILD_DEFAULT_CA_BUNDLE)


def create_default_context(
    purpose=Purpose.SERVER_AUTH, *, cafile=None, capath=None, cadata=None
):
    if not isinstance(purpose, _constants._ASN1ObjectBase):
        raise TypeError(purpose)
    if purpose == Purpose.SERVER_AUTH:
        context = SSLContext(PROTOCOL_TLS_CLIENT)
        context.verify_mode = CERT_REQUIRED
        context.check_hostname = True
        context.verify_flags |= (
            VERIFY_X509_PARTIAL_CHAIN
            | VERIFY_X509_STRICT
            | VERIFY_X509_TRUSTED_FIRST
        )
    elif purpose == Purpose.CLIENT_AUTH:
        context = SSLContext(PROTOCOL_TLS_SERVER)
    else:
        raise ValueError(purpose)
    if cafile or capath or cadata:
        context.load_verify_locations(cafile, capath, cadata)
    else:
        context.load_default_certs(purpose)
    if hasattr(context, "keylog_filename"):
        keylogfile = os.environ.get("SSLKEYLOGFILE")
        if keylogfile and not sys.flags.ignore_environment:
            context.keylog_filename = keylogfile
    return context


def _create_unverified_context(
    protocol=None,
    *,
    cert_reqs=CERT_NONE,
    check_hostname=False,
    purpose=Purpose.SERVER_AUTH,
    certfile=None,
    keyfile=None,
    cafile=None,
    capath=None,
    cadata=None,
):
    if not isinstance(purpose, _constants._ASN1ObjectBase):
        raise TypeError(purpose)
    if purpose not in (Purpose.SERVER_AUTH, Purpose.CLIENT_AUTH):
        raise ValueError(purpose)
    if protocol is None:
        protocol = (
            PROTOCOL_TLS_CLIENT
            if purpose == Purpose.SERVER_AUTH
            else PROTOCOL_TLS_SERVER
        )
    context = SSLContext(protocol)
    context.check_hostname = check_hostname
    if cert_reqs is not None:
        context.verify_mode = cert_reqs
    if keyfile and not certfile:
        raise ValueError("certfile must be specified")
    if certfile or keyfile:
        context.load_cert_chain(certfile, keyfile)
    if cafile or capath or cadata:
        context.load_verify_locations(cafile, capath, cadata)
    elif context.verify_mode != CERT_NONE:
        context.load_default_certs(purpose)
    keylogfile = os.environ.get("SSLKEYLOGFILE")
    if keylogfile and not sys.flags.ignore_environment:
        context.keylog_filename = keylogfile
    return context


_create_stdlib_context = _create_unverified_context
_create_default_https_context = create_default_context

from _ssl_metadata import _TLSContentType, _TLSAlertType, _TLSMessageType
import _ssl
from rtls import _constants

from _ssl import OPENSSL_API_VERSION_INFO
_OPENSSL_API_VERSION = OPENSSL_API_VERSION_INFO

HAS_PSK_TLS13 = False

_IntEnum._convert_(
    "_SSLMethod",
    __name__,
    lambda name: name.startswith("PROTOCOL_") and name != "PROTOCOL_SSLv23",
    source=_ssl,
)
_IntFlag._convert_(
    "Options", __name__, lambda name: name.startswith("OP_"), source=_ssl
)
_IntEnum._convert_(
    "AlertDescription",
    __name__,
    lambda name: name.startswith("ALERT_DESCRIPTION_"),
    source=_ssl,
)
_IntEnum._convert_(
    "SSLErrorNumber",
    __name__,
    lambda name: name.startswith("SSL_ERROR_"),
    source=_ssl,
)
_IntFlag._convert_(
    "VerifyFlags", __name__, lambda name: name.startswith("VERIFY_"), source=_ssl
)
_IntEnum._convert_(
    "VerifyMode", __name__, lambda name: name.startswith("CERT_"), source=_ssl
)

PROTOCOL_SSLv23 = _SSLMethod.PROTOCOL_SSLv23 = _SSLMethod.PROTOCOL_TLS
_PROTOCOL_NAMES = frozendict(
    {
        value: name for name, value in _SSLMethod.__members__.items()
    }
)
_SSLv2_IF_EXISTS = getattr(_SSLMethod, "PROTOCOL_SSLv2", None)
__all__.extend(
    (
        "SSLErrorNumber",
        "ALERT_DESCRIPTION_CERTIFICATE_UNOBTAINABLE",
        "ALERT_DESCRIPTION_UNRECOGNIZED_NAME",
        "ALERT_DESCRIPTION_BAD_CERTIFICATE_STATUS_RESPONSE",
        "ALERT_DESCRIPTION_BAD_CERTIFICATE_HASH_VALUE",
        "ALERT_DESCRIPTION_UNKNOWN_PSK_IDENTITY",
    )
)


def get_protocol_name(protocol_code):
    return _PROTOCOL_NAMES.get(protocol_code, "<unknown>")


__all__.append("get_protocol_name")
