pub(crate) fn mock_timestamp(tick: u64) -> String {
    let seconds = tick % 60;
    let minutes = (tick / 60) % 60;
    let hours = (tick / 3600) % 24;
    format!("1970-01-01T{hours:02}:{minutes:02}:{seconds:02}Z")
}

#[cfg(test)]
mod tests {
    use super::mock_timestamp;

    #[test]
    fn mock_timestamp_is_deterministic_and_zero_padded() {
        assert_eq!(mock_timestamp(1), "1970-01-01T00:00:01Z");
        assert_eq!(mock_timestamp(61), "1970-01-01T00:01:01Z");
        assert_eq!(mock_timestamp(3661), "1970-01-01T01:01:01Z");
    }
}
