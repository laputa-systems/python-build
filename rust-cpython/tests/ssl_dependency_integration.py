import os
from pathlib import Path
import ssl
import tempfile
import test


def main():
    fixtures = Path(__file__).with_name("fixtures")
    certdata = Path(test.__file__).with_name("certdata")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_cert_chain(str(certdata / "ssl_cert.pem"), str(fixtures / "encrypted-key-sha256.pem"),
                            "dependency-test")
    for password in (None, "wrong"):
        try:
            context.load_cert_chain(str(certdata / "ssl_cert.pem"), str(fixtures / "encrypted-key-sha256.pem"),
                                    password)
        except ssl.SSLError:
            pass
        else:
            raise AssertionError(f"encrypted key accepted password {password!r}")

    original = (certdata / "allsans.pem").read_text()
    start = original.index("-----BEGIN CERTIFICATE-----")
    end = original.index("-----END CERTIFICATE-----", start) + len("-----END CERTIFICATE-----")
    original = original[start:end]
    decoded = ssl._ssl._test_decode_cert(str(certdata / "allsans.pem"))
    assert len(decoded["subjectAltName"]) == 10, decoded
    der = bytearray(ssl.PEM_cert_to_DER_cert(original))
    needle = b"\x82\x07allsans"
    assert der.count(needle) == 1
    der[der.index(needle) + 2] = 0xff
    with tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"]) as scratch:
        path = Path(scratch) / "invalid-san.pem"
        path.write_text(ssl.DER_cert_to_PEM_cert(der))
        decoded = ssl._ssl._test_decode_cert(str(path))
    assert "subjectAltName" not in decoded, decoded
    print("OK    encrypted key loading and invalid-SAN handling preserve prior behavior")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
