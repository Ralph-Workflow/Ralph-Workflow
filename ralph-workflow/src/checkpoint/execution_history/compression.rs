/// Compress `data` to a base64-encoded gzip string.
///
/// Delegates to the boundary-layer implementation in `checkpoint::io::compression`,
/// which owns all I/O state (encoder, buffers).
pub fn compress(data: &[u8]) -> Result<String, std::io::Error> {
    crate::checkpoint::io::compression::compress(data)
}

/// Decompress a base64-encoded gzip string back to UTF-8.
///
/// Delegates to the boundary-layer implementation in `checkpoint::io::compression`,
/// which owns all I/O state (decoder, buffers) and enforces the size cap.
pub fn decompress(encoded: &str) -> Result<String, std::io::Error> {
    crate::checkpoint::io::compression::decompress(encoded)
}
