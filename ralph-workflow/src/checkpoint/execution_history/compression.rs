//! Compression and decompression for file snapshots.
//!
//! This is a boundary module - compression/decompression inherently requires
//! mutable state and byte-by-byte I/O, which is allowed here.

const MAX_DECOMPRESSED_SNAPSHOT_BYTES: usize = 1024 * 1024;

/// Compress data using gzip and return base64-encoded result.
pub fn compress(data: &[u8]) -> Result<String, std::io::Error> {
    use base64::{engine::general_purpose::STANDARD, Engine};
    use flate2::write::GzEncoder;
    use flate2::Compression;
    use std::io::Write;

    let mut encoder = GzEncoder::new(Vec::new(), Compression::default());
    encoder.write_all(data)?;
    let compressed = encoder.finish()?;

    Ok(STANDARD.encode(&compressed))
}

/// Decompress base64-encoded gzip data.
pub fn decompress(encoded: &str) -> Result<String, std::io::Error> {
    use base64::{engine::general_purpose::STANDARD, Engine};
    use flate2::read::GzDecoder;
    use std::io::Read;

    let compressed = STANDARD.decode(encoded).map_err(|e| {
        std::io::Error::new(
            std::io::ErrorKind::InvalidData,
            format!("Base64 decode error: {e}"),
        )
    })?;

    let mut decoder = GzDecoder::new(compressed.as_slice());
    let mut decompressed = Vec::new();
    let mut buf = [0u8; 8 * 1024];

    loop {
        let n = decoder.read(&mut buf)?;
        if n == 0 {
            break;
        }

        if decompressed.len().saturating_add(n) > MAX_DECOMPRESSED_SNAPSHOT_BYTES {
            return Err(std::io::Error::new(
                std::io::ErrorKind::InvalidData,
                format!(
                    "Decompressed payload exceeds max size ({MAX_DECOMPRESSED_SNAPSHOT_BYTES} bytes)"
                ),
            ));
        }

        decompressed.extend_from_slice(&buf[..n]);
    }

    String::from_utf8(decompressed).map_err(|e| {
        std::io::Error::new(
            std::io::ErrorKind::InvalidData,
            format!("UTF-8 decode error: {e}"),
        )
    })
}
