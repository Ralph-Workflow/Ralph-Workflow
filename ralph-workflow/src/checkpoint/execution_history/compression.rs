const MAX_DECOMPRESSED_SNAPSHOT_BYTES: usize = 1024 * 1024;

pub fn compress(data: &[u8]) -> Result<String, std::io::Error> {
    use base64::{engine::general_purpose::STANDARD, Engine};
    use flate2::write::GzEncoder;
    use flate2::Compression;

    let mut encoder = GzEncoder::new(Vec::new(), Compression::default());
    std::io::Write::write_all(&mut encoder, data)?;
    let compressed = encoder.finish()?;

    Ok(STANDARD.encode(&compressed))
}

pub fn decompress(encoded: &str) -> Result<String, std::io::Error> {
    use base64::{engine::general_purpose::STANDARD, Engine};
    use flate2::read::GzDecoder;

    let compressed = STANDARD.decode(encoded).map_err(|error| {
        std::io::Error::new(
            std::io::ErrorKind::InvalidData,
            format!("Base64 decode error: {error}"),
        )
    })?;

    let mut decoder = GzDecoder::new(compressed.as_slice());
    let mut decompressed = Vec::new();
    let mut buffer = [0_u8; 8 * 1024];

    loop {
        let read = std::io::Read::read(&mut decoder, &mut buffer)?;
        if read == 0 {
            break;
        }

        if decompressed.len().saturating_add(read) > MAX_DECOMPRESSED_SNAPSHOT_BYTES {
            return Err(std::io::Error::new(
                std::io::ErrorKind::InvalidData,
                format!(
                    "Decompressed payload exceeds max size ({MAX_DECOMPRESSED_SNAPSHOT_BYTES} bytes)"
                ),
            ));
        }

        decompressed.extend_from_slice(&buffer[..read]);
    }

    String::from_utf8(decompressed).map_err(|error| {
        std::io::Error::new(
            std::io::ErrorKind::InvalidData,
            format!("UTF-8 decode error: {error}"),
        )
    })
}
