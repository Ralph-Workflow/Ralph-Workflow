const MAX_DECOMPRESSED_SNAPSHOT_BYTES: usize = 1024 * 1024;

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

fn base64_decode(encoded: &str) -> Result<Vec<u8>, std::io::Error> {
    use base64::{engine::general_purpose::STANDARD, Engine};
    STANDARD.decode(encoded).map_err(|e| {
        std::io::Error::new(
            std::io::ErrorKind::InvalidData,
            format!("Base64 decode error: {e}"),
        )
    })
}

fn check_size_limit(current_len: usize, n: usize) -> Result<(), std::io::Error> {
    if current_len.saturating_add(n) > MAX_DECOMPRESSED_SNAPSHOT_BYTES {
        return Err(std::io::Error::new(
            std::io::ErrorKind::InvalidData,
            format!(
                "Decompressed payload exceeds max size ({MAX_DECOMPRESSED_SNAPSHOT_BYTES} bytes)"
            ),
        ));
    }
    Ok(())
}

fn read_chunk<R: std::io::Read>(
    reader: &mut R,
    buf: &mut [u8],
    decompressed: &mut Vec<u8>,
) -> Result<bool, std::io::Error> {
    let n = reader.read(buf)?;
    if n == 0 {
        return Ok(false);
    }
    check_size_limit(decompressed.len(), n)?;
    decompressed.extend_from_slice(&buf[..n]);
    Ok(true)
}

fn gz_decompress(compressed: &[u8]) -> Result<Vec<u8>, std::io::Error> {
    use flate2::read::GzDecoder;

    let mut decoder = GzDecoder::new(compressed);
    let mut decompressed = Vec::new();
    let mut buf = [0u8; 8 * 1024];

    while read_chunk(&mut decoder, &mut buf, &mut decompressed)? {}

    Ok(decompressed)
}

fn bytes_to_utf8(decompressed: Vec<u8>) -> Result<String, std::io::Error> {
    String::from_utf8(decompressed).map_err(|e| {
        std::io::Error::new(
            std::io::ErrorKind::InvalidData,
            format!("UTF-8 decode error: {e}"),
        )
    })
}

pub fn decompress(encoded: &str) -> Result<String, std::io::Error> {
    let compressed = base64_decode(encoded)?;
    let decompressed = gz_decompress(&compressed)?;
    bytes_to_utf8(decompressed)
}
