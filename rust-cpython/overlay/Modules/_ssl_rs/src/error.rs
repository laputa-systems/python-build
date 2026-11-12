use rustls::AlertDescription;

pub(crate) const ZERO_RETURN_MESSAGE: &str = "TLS/SSL connection has been closed by the peer";
pub(crate) const NO_SHARED_CIPHER_MESSAGE: &str = "[SSL: NO_SHARED_CIPHER] no shared cipher";
pub(crate) const NO_SHARED_CIPHER_REASON: &str = "NO_SHARED_CIPHER";

pub(crate) enum TlsError {
    CertVerification { message: String, code: i32 },
    General(String),
    AlertReceived(rustls::AlertDescription),
    NoSharedCipher,
    CleanEof,
}

fn verify_code_from_webpki(error: &webpki::Error) -> i32 {
    use webpki::CertificatePolicyViolation as Violation;

    match error {
        webpki::Error::CertExpired { .. } => 10,
        webpki::Error::CertNotValidForName(_) => 62,
        webpki::Error::CertNotValidYet { .. } => 9,
        webpki::Error::CertRevoked => 23,
        webpki::Error::CertDistrusted => 28,
        webpki::Error::CrlNotYetValid { .. } => 11,
        webpki::Error::CrlExpired { .. } => 12,
        webpki::Error::InvalidCrlSignatureForPublicKey => 8,
        webpki::Error::EmptyEkuExtension
        | webpki::Error::RequiredEkuNotFoundContext(_) => 26,
        webpki::Error::ExtensionValueInvalid => 41,
        webpki::Error::EndEntityUsedAsCa => 79,
        webpki::Error::PathLenConstraintViolated => 25,
        webpki::Error::CertificatePolicyViolation(violation) => match violation {
            Violation::InvalidCaCertificate => 79,
            Violation::PathLengthOnNonCaCertificate => 80,
            Violation::PathLengthWithoutKeyCertSign => 81,
            Violation::KeyCertSignOnNonCaCertificate => 82,
            Violation::EmptyIssuerName => 83,
            Violation::EmptySubjectName => 84,
            Violation::MissingAuthorityKeyIdentifier => 85,
            Violation::MissingSubjectKeyIdentifier => 86,
            Violation::EmptySubjectAlternativeName => 87,
            Violation::EmptySubjectWithNoncriticalSubjectAlternativeName => 88,
            Violation::NoncriticalCaBasicConstraints => 89,
            Violation::CriticalAuthorityKeyIdentifier => 90,
            Violation::CriticalSubjectKeyIdentifier => 91,
            Violation::CaMissingKeyUsage => 92,
            Violation::ExtensionsRequireX509V3 => 93,
            Violation::SignatureAlgorithmMismatch => 78,
            Violation::InvalidPurpose => 26,
        },
        webpki::Error::UnsupportedSignatureAlgorithmContext(context)
            if is_rsa_sha1_algorithm(&context.signature_algorithm_id) => 68,
        webpki::Error::InvalidSignatureForPublicKey => 7,
        webpki::Error::UnknownIssuer => 20,
        webpki::Error::UnknownRevocationStatus => 3,
        webpki::Error::UnsupportedCriticalExtension => 34,
        _ => 1,
    }
}

fn is_rsa_sha1_algorithm(algorithm: &[u8]) -> bool {
    const RSA_SHA1_WITH_NULL: &[u8] = &[
        0x30, 0x0d, 0x06, 0x09, 0x2a, 0x86, 0x48, 0x86, 0xf7, 0x0d, 0x01, 0x01, 0x05, 0x05, 0x00,
    ];
    const RSA_SHA1_WITHOUT_NULL: &[u8] = &[
        0x30, 0x0b, 0x06, 0x09, 0x2a, 0x86, 0x48, 0x86, 0xf7, 0x0d, 0x01, 0x01, 0x05,
    ];
    const RSA_SHA1_CONTENT_WITH_NULL: &[u8] = &[
        0x06, 0x09, 0x2a, 0x86, 0x48, 0x86, 0xf7, 0x0d, 0x01, 0x01, 0x05, 0x05, 0x00,
    ];
    const RSA_SHA1_CONTENT_WITHOUT_NULL: &[u8] = &[
        0x06, 0x09, 0x2a, 0x86, 0x48, 0x86, 0xf7, 0x0d, 0x01, 0x01, 0x05,
    ];

    matches!(
        algorithm,
        RSA_SHA1_WITH_NULL
            | RSA_SHA1_WITHOUT_NULL
            | RSA_SHA1_CONTENT_WITH_NULL
            | RSA_SHA1_CONTENT_WITHOUT_NULL
    )
}

fn verify_code_from_certificate_error(error: &rustls::CertificateError) -> i32 {
    use rustls::CertificateError;

    match error {
        CertificateError::Expired | CertificateError::ExpiredContext { .. } => 10,
        CertificateError::NotValidYet | CertificateError::NotValidYetContext { .. } => 9,
        CertificateError::Revoked => 23,
        CertificateError::UnhandledCriticalExtension => 34,
        CertificateError::UnknownIssuer => 20,
        CertificateError::UnknownRevocationStatus => 3,
        CertificateError::ExpiredRevocationList
        | CertificateError::ExpiredRevocationListContext { .. } => 12,
        CertificateError::BadSignature => 7,
        CertificateError::NotValidForName | CertificateError::NotValidForNameContext { .. } => 62,
        CertificateError::InvalidPurpose | CertificateError::InvalidPurposeContext { .. } => 26,
        CertificateError::Other(other) => other
            .0
            .downcast_ref::<webpki::Error>()
            .map_or(1, verify_code_from_webpki),
        _ => 1,
    }
}

impl From<rustls::Error> for TlsError {
    fn from(err: rustls::Error) -> Self {
        match err {
            rustls::Error::AlertReceived(alert) => TlsError::AlertReceived(alert),
            rustls::Error::PeerIncompatible(
                rustls::PeerIncompatible::NoCipherSuitesInCommon,
            ) => TlsError::NoSharedCipher,
            rustls::Error::InvalidCertificate(cert_error) => TlsError::CertVerification {
                message: format!("invalid peer certificate: {}", cert_error),
                code: verify_code_from_certificate_error(&cert_error),
            },
            rustls::Error::NoCertificatesPresented => TlsError::CertVerification {
                message: "no certificates presented".to_string(),
                code: 1,
            },
            _ => TlsError::General(format!("{}", err)),
        }
    }
}

fn alert_error(alert: AlertDescription) -> Option<(&'static str, &'static str)> {
    match alert {
        AlertDescription::UnexpectedMessage => Some((
            "SSLV3_ALERT_UNEXPECTED_MESSAGE",
            "ssl/tls alert unexpected message",
        )),
        AlertDescription::BadRecordMac => {
            Some(("SSLV3_ALERT_BAD_RECORD_MAC", "ssl/tls alert bad record mac"))
        }
        AlertDescription::DecryptionFailed => Some((
            "TLSV1_ALERT_DECRYPTION_FAILED",
            "tlsv1 alert decryption failed",
        )),
        AlertDescription::RecordOverflow => Some((
            "TLSV1_ALERT_RECORD_OVERFLOW",
            "tlsv1 alert record overflow",
        )),
        AlertDescription::DecompressionFailure => Some((
            "SSLV3_ALERT_DECOMPRESSION_FAILURE",
            "ssl/tls alert decompression failure",
        )),
        AlertDescription::HandshakeFailure => Some((
            "SSLV3_ALERT_HANDSHAKE_FAILURE",
            "ssl/tls alert handshake failure",
        )),
        AlertDescription::NoCertificate => {
            Some(("SSLV3_ALERT_NO_CERTIFICATE", "ssl/tls alert no certificate"))
        }
        AlertDescription::BadCertificate => {
            Some(("SSLV3_ALERT_BAD_CERTIFICATE", "ssl/tls alert bad certificate"))
        }
        AlertDescription::UnsupportedCertificate => Some((
            "SSLV3_ALERT_UNSUPPORTED_CERTIFICATE",
            "ssl/tls alert unsupported certificate",
        )),
        AlertDescription::CertificateRevoked => Some((
            "SSLV3_ALERT_CERTIFICATE_REVOKED",
            "ssl/tls alert certificate revoked",
        )),
        AlertDescription::CertificateExpired => Some((
            "SSLV3_ALERT_CERTIFICATE_EXPIRED",
            "ssl/tls alert certificate expired",
        )),
        AlertDescription::CertificateUnknown => Some((
            "SSLV3_ALERT_CERTIFICATE_UNKNOWN",
            "ssl/tls alert certificate unknown",
        )),
        AlertDescription::IllegalParameter => Some((
            "SSLV3_ALERT_ILLEGAL_PARAMETER",
            "ssl/tls alert illegal parameter",
        )),
        AlertDescription::UnknownCA => {
            Some(("TLSV1_ALERT_UNKNOWN_CA", "tlsv1 alert unknown ca"))
        }
        AlertDescription::AccessDenied => {
            Some(("TLSV1_ALERT_ACCESS_DENIED", "tlsv1 alert access denied"))
        }
        AlertDescription::DecodeError => {
            Some(("TLSV1_ALERT_DECODE_ERROR", "tlsv1 alert decode error"))
        }
        AlertDescription::DecryptError => {
            Some(("TLSV1_ALERT_DECRYPT_ERROR", "tlsv1 alert decrypt error"))
        }
        AlertDescription::ExportRestriction => Some((
            "TLSV1_ALERT_EXPORT_RESTRICTION",
            "tlsv1 alert export restriction",
        )),
        AlertDescription::ProtocolVersion => Some((
            "TLSV1_ALERT_PROTOCOL_VERSION",
            "tlsv1 alert protocol version",
        )),
        AlertDescription::InsufficientSecurity => Some((
            "TLSV1_ALERT_INSUFFICIENT_SECURITY",
            "tlsv1 alert insufficient security",
        )),
        AlertDescription::InternalError => {
            Some(("TLSV1_ALERT_INTERNAL_ERROR", "tlsv1 alert internal error"))
        }
        AlertDescription::InappropriateFallback => Some((
            "TLSV1_ALERT_INAPPROPRIATE_FALLBACK",
            "tlsv1 alert inappropriate fallback",
        )),
        AlertDescription::UserCanceled => {
            Some(("TLSV1_ALERT_USER_CANCELLED", "tlsv1 alert user cancelled"))
        }
        AlertDescription::NoRenegotiation => Some((
            "TLSV1_ALERT_NO_RENEGOTIATION",
            "tlsv1 alert no renegotiation",
        )),
        AlertDescription::MissingExtension => Some((
            "TLSV13_ALERT_MISSING_EXTENSION",
            "tlsv13 alert missing extension",
        )),
        AlertDescription::UnsupportedExtension => {
            Some(("TLSV1_UNSUPPORTED_EXTENSION", "tlsv1 unsupported extension"))
        }
        AlertDescription::CertificateUnobtainable => Some((
            "TLSV1_CERTIFICATE_UNOBTAINABLE",
            "tlsv1 certificate unobtainable",
        )),
        AlertDescription::UnrecognisedName => {
            Some(("TLSV1_UNRECOGNIZED_NAME", "tlsv1 unrecognized name"))
        }
        AlertDescription::BadCertificateStatusResponse => Some((
            "TLSV1_BAD_CERTIFICATE_STATUS_RESPONSE",
            "tlsv1 bad certificate status response",
        )),
        AlertDescription::BadCertificateHashValue => Some((
            "TLSV1_BAD_CERTIFICATE_HASH_VALUE",
            "tlsv1 bad certificate hash value",
        )),
        AlertDescription::UnknownPSKIdentity => Some((
            "TLSV1_ALERT_UNKNOWN_PSK_IDENTITY",
            "tlsv1 alert unknown psk identity",
        )),
        AlertDescription::CertificateRequired => Some((
            "TLSV13_ALERT_CERTIFICATE_REQUIRED",
            "tlsv13 alert certificate required",
        )),
        AlertDescription::NoApplicationProtocol => Some((
            "TLSV1_ALERT_NO_APPLICATION_PROTOCOL",
            "tlsv1 alert no application protocol",
        )),
        _ => None,
    }
}

pub(crate) fn alert_error_fields(alert: AlertDescription) -> (Option<&'static str>, String) {
    match alert_error(alert) {
        Some((reason, description)) => (
            Some(reason),
            format!("[SSL: {reason}] {description}"),
        ),
        None => {
            let error_code = (20_u64 << 23) | (1000 + u64::from(u8::from(alert)));
            (None, format!("[SSL] unknown error (0x{error_code:x})"))
        }
    }
}

pub(crate) fn certificate_verification_message(message: &str, verify_code: i32) -> &str {
    match verify_code {
        3 => "unable to get certificate CRL",
        7 => "certificate signature failure",
        8 => "CRL signature failure",
        9 => "certificate is not yet valid",
        10 => "certificate has expired",
        11 => "CRL is not yet valid",
        12 => "CRL has expired",
        20 => "unable to get local issuer certificate",
        23 => "certificate revoked",
        25 => "path length constraint exceeded",
        26 => "unsuitable certificate purpose",
        28 => "certificate rejected",
        34 => "unhandled critical extension",
        68 => "CA signature digest algorithm too weak",
        78 => "Signature algorithm mismatch",
        79 => "invalid CA certificate",
        80 => "Path length invalid for non-CA cert",
        81 => "Path length given without key usage keyCertSign",
        82 => "Key usage keyCertSign invalid for non-CA cert",
        83 => "Issuer name empty",
        84 => "Subject name empty",
        85 => "Missing Authority Key Identifier",
        86 => "Missing Subject Key Identifier",
        87 => "Empty Subject Alternative Name extension",
        88 => "Subject empty and Subject Alt Name extension not critical",
        89 => "Basic Constraints of CA cert not marked critical",
        90 => "Authority Key Identifier marked critical",
        91 => "Subject Key Identifier marked critical",
        92 => "CA cert does not include key usage extension",
        93 => "Using cert extension requires at least X509v3",
        41 => "invalid certificate extension",
        62 => "Hostname mismatch",
        _ => message,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn distrusted_certificate_is_rejected_with_openssl_code() {
        let other = rustls::OtherError(std::sync::Arc::new(webpki::Error::CertDistrusted));
        let error = rustls::Error::InvalidCertificate(rustls::CertificateError::Other(other));
        let TlsError::CertVerification { message, code } = TlsError::from(error) else {
            panic!("distrusted certificate must fail verification");
        };
        assert_eq!(code, 28);
        assert_eq!(certificate_verification_message(&message, code), "certificate rejected");
    }
}
