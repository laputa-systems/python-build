use x509_parser::extensions::*;
use x509_parser::prelude::*;

/// Intermediate Rust representation of a parsed certificate.
/// Built without the GIL, then converted to Python objects with the GIL.
pub(crate) struct ParsedCert {
    pub(crate) version: u32,
    pub(crate) serial: String,
    pub(crate) issuer: Vec<Vec<(String, String)>>,
    pub(crate) subject: Vec<Vec<(String, String)>>,
    pub(crate) not_before: String,
    pub(crate) not_after: String,
    pub(crate) san: Option<Vec<ParsedSanName>>,
    pub(crate) ocsp: Vec<String>,
    pub(crate) ca_issuers: Vec<String>,
    pub(crate) crl_distribution_points: Vec<String>,
}

pub(crate) struct ParsedSanName {
    pub(crate) kind: &'static str,
    pub(crate) value: ParsedSanValue,
}

pub(crate) enum ParsedSanValue {
    Text(String),
    DirectoryName(Vec<Vec<(String, String)>>),
}

/// Phase 1: Pure Rust DER parsing — no Python objects, no GIL needed.
pub(crate) fn parse_cert_to_rust(der_bytes: &[u8]) -> Result<ParsedCert, String> {
    let (_, cert) = X509Certificate::from_der(der_bytes)
        .map_err(|e| format!("Failed to parse certificate: {}", e))?;

    let version = cert.version().0 + 1;
    let serial_bytes = cert.raw_serial();
    let serial_bytes = if serial_bytes.len() > 1
        && serial_bytes[0] == 0
        && serial_bytes[1] & 0x80 != 0
    {
        &serial_bytes[1..]
    } else {
        serial_bytes
    };
    let serial = serial_bytes
        .iter()
        .map(|byte| format!("{byte:02X}"))
        .collect::<String>();
    let issuer = x500_name_to_rust(cert.issuer());
    let subject = x500_name_to_rust(cert.subject());
    let not_before = format_asn1_time(&cert.validity().not_before);
    let not_after = format_asn1_time(&cert.validity().not_after);

    let mut san = None;
    let mut ocsp = Vec::new();
    let mut ca_issuers = Vec::new();
    let mut crl_distribution_points = Vec::new();

    'extensions: for ext in cert.extensions() {
        match ext.parsed_extension() {
            ParsedExtension::SubjectAlternativeName(san_ext) => {
                let mut entries = Vec::new();
                for name in &san_ext.general_names {
                    let entry = match name {
                        GeneralName::DNSName(dns) => ParsedSanName {
                            kind: "DNS",
                            value: ParsedSanValue::Text(dns.to_string()),
                        },
                        GeneralName::IPAddress(ip) => ParsedSanName {
                            kind: "IP Address",
                            value: ParsedSanValue::Text(format_ip_address(ip)),
                        },
                        GeneralName::RFC822Name(email) => ParsedSanName {
                            kind: "email",
                            value: ParsedSanValue::Text(email.to_string()),
                        },
                        GeneralName::URI(uri) => ParsedSanName {
                            kind: "URI",
                            value: ParsedSanValue::Text(uri.to_string()),
                        },
                        GeneralName::OtherName(_, _) => ParsedSanName {
                            kind: "othername",
                            value: ParsedSanValue::Text("<unsupported>".to_string()),
                        },
                        GeneralName::DirectoryName(name) => ParsedSanName {
                            kind: "DirName",
                            value: ParsedSanValue::DirectoryName(
                                x500_name_to_rust(name),
                            ),
                        },
                        GeneralName::RegisteredID(oid) => ParsedSanName {
                            kind: "Registered ID",
                            value: ParsedSanValue::Text(oid.to_id_string()),
                        },
                        GeneralName::X400Address(_) | GeneralName::EDIPartyName(_) => {
                            continue;
                        }
                        // Earlier parser versions rejected the entire SAN extension on invalid data.
                        GeneralName::Invalid(_, _) => continue 'extensions,
                    };
                    entries.push(entry);
                }
                san = Some(entries);
            }
            ParsedExtension::AuthorityInfoAccess(aia) => {
                for access_desc in &aia.accessdescs {
                    let uri = match &access_desc.access_location {
                        GeneralName::URI(u) => Some(u.to_string()),
                        _ => None,
                    };
                    if let Some(uri) = uri {
                        let oid_str = access_desc.access_method.to_id_string();
                        if oid_str == "1.3.6.1.5.5.7.48.1" {
                            ocsp.push(uri);
                        } else if oid_str == "1.3.6.1.5.5.7.48.2" {
                            ca_issuers.push(uri);
                        }
                    }
                }
            }
            ParsedExtension::CRLDistributionPoints(crl_dps) => {
                for dp in &crl_dps.points {
                    if let Some(DistributionPointName::FullName(names)) = &dp.distribution_point {
                        for name in names {
                            if let GeneralName::URI(uri) = name {
                                crl_distribution_points.push(uri.to_string());
                            }
                        }
                    }
                }
            }
            _ => {}
        }
    }

    Ok(ParsedCert {
        version,
        serial,
        issuer,
        subject,
        not_before,
        not_after,
        san,
        ocsp,
        ca_issuers,
        crl_distribution_points,
    })
}

/// Convert X.500 Name to a Rust intermediate representation.
fn x500_name_to_rust(name: &X509Name) -> Vec<Vec<(String, String)>> {
    name.iter()
        .map(|rdn| {
            rdn.iter()
                .map(|attr| {
                    let oid_name = oid_to_name(attr.attr_type()).to_string();
                    let value = attr.as_str().unwrap_or("<binary>").to_string();
                    (oid_name, value)
                })
                .collect()
        })
        .collect()
}

/// Map common OID to human-readable name (matching CPython ssl output).
fn oid_to_name(oid: &x509_parser::der_parser::oid::Oid) -> &'static str {
    let oid_str = oid.to_id_string();
    match oid_str.as_str() {
        "2.5.4.3" => "commonName",
        "2.5.4.6" => "countryName",
        "2.5.4.7" => "localityName",
        "2.5.4.8" => "stateOrProvinceName",
        "2.5.4.10" => "organizationName",
        "2.5.4.11" => "organizationalUnitName",
        "2.5.4.5" => "serialNumber",
        "2.5.4.12" => "title",
        "2.5.4.42" => "givenName",
        "2.5.4.4" => "surname",
        "2.5.4.46" => "dnQualifier",
        "2.5.4.65" => "pseudonym",
        "1.2.840.113549.1.9.1" => "emailAddress",
        "0.9.2342.19200300.100.1.25" => "domainComponent",
        "2.5.4.9" => "streetAddress",
        "2.5.4.17" => "postalCode",
        "0.9.2342.19200300.100.1.1" => "userId",
        "2.5.4.45" => "uniqueIdentifier",
        "1.3.6.1.4.1.311.60.2.1.3" => "jurisdictionCountryName",
        "1.3.6.1.4.1.311.60.2.1.2" => "jurisdictionStateOrProvinceName",
        "1.3.6.1.4.1.311.60.2.1.1" => "jurisdictionLocalityName",
        "2.5.4.15" => "businessCategory",
        _ => "unknown",
    }
}

/// Format an IP address from raw bytes (4 bytes = IPv4, 16 bytes = IPv6).
fn format_ip_address(bytes: &[u8]) -> String {
    match bytes.len() {
        4 => format!("{}.{}.{}.{}", bytes[0], bytes[1], bytes[2], bytes[3]),
        16 => {
            let mut parts = Vec::new();
            for i in 0..8 {
                parts.push(format!(
                    "{:X}",
                    u16::from_be_bytes([bytes[i * 2], bytes[i * 2 + 1]])
                ));
            }
            parts.join(":")
        }
        _ => format!("<{} bytes>", bytes.len()),
    }
}

/// Format ASN1 time to the OpenSSL-style string: "Jan  1 00:00:00 2024 GMT"
fn format_asn1_time(time: &ASN1Time) -> String {
    let dt = time.to_datetime();
    let months = [
        "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    ];
    let month_idx = dt.month() as usize;
    let month = if (1..=12).contains(&month_idx) {
        months[month_idx - 1]
    } else {
        "???"
    };
    format!(
        "{} {:2} {:02}:{:02}:{:02} {:04} GMT",
        month,
        dt.day(),
        dt.hour(),
        dt.minute(),
        dt.second(),
        dt.year()
    )
}
