pub struct Config {
    pub port: u16,
    pub ntfy_base_url: String,
    pub ntfy_topic: String,
}

impl Config {
    pub fn from_env() -> Self {
        Self {
            port: std::env::var("PORT")
                .unwrap_or_else(|_| "8081".to_string())
                .parse()
                .expect("PORT must be a number"),
            ntfy_base_url: std::env::var("NTFY_BASE_URL")
                .unwrap_or_else(|_| "https://ntfy.sh".to_string()),
            ntfy_topic: std::env::var("NTFY_TOPIC")
                .unwrap_or_else(|_| "belgrade-os".to_string()),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_defaults() {
        // Clean environment before testing defaults
        std::env::remove_var("PORT");
        std::env::remove_var("NTFY_BASE_URL");
        std::env::remove_var("NTFY_TOPIC");

        let cfg = Config::from_env();
        assert_eq!(cfg.port, 8081);
        assert_eq!(cfg.ntfy_base_url, "https://ntfy.sh");
        assert_eq!(cfg.ntfy_topic, "belgrade-os");

        // Clean up after ourselves
        std::env::remove_var("PORT");
        std::env::remove_var("NTFY_BASE_URL");
        std::env::remove_var("NTFY_TOPIC");
    }

    #[test]
    fn test_from_env() {
        // Clean before setting
        std::env::remove_var("PORT");
        std::env::remove_var("NTFY_TOPIC");

        std::env::set_var("PORT", "9090");
        std::env::set_var("NTFY_TOPIC", "my-topic");
        let cfg = Config::from_env();
        assert_eq!(cfg.port, 9090);
        assert_eq!(cfg.ntfy_topic, "my-topic");

        // Clean up after ourselves
        std::env::remove_var("PORT");
        std::env::remove_var("NTFY_TOPIC");
    }
}
