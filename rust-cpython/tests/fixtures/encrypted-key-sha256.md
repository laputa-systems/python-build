`encrypted-key-sha256.pem` is the public CPython `Lib/test/certdata/ssl_key.pem`
test key from the pinned e891a911 source, encrypted with AES-256-CBC and
PBKDF2-HMAC-SHA256 (10,000 iterations). Its test password is `dependency-test`.
It matches CPython's `ssl_cert.pem` and is covered by the source's PSF license.
