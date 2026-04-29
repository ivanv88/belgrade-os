#[derive(Debug)]
pub struct Config {
    pub port: u16,
    pub ntfy_base_url: String,
    pub ntfy_topic: String,
}

impl Config {
    pub fn from_env() -> Self {
        Self::from_map(|key| std::env::var(key).ok())
    }

    pub fn from_map<F: Fn(&str) -> Option<String>>(lookup: F) -> Self {
        Self {
            port: lookup("PORT")
                .unwrap_or_else(|| "8081".to_string())
                .parse::<u16>()
                .unwrap_or_else(|_| {
                    let raw = lookup("PORT").unwrap_or_default();
                    panic!("PORT must be a number in 0-65535, got {:?}", raw)
                }),
            ntfy_base_url: lookup("NTFY_BASE_URL")
                .unwrap_or_else(|| "https://ntfy.sh".to_string()),
            ntfy_topic: lookup("NTFY_TOPIC")
                .unwrap_or_else(|| "belgrade-os".to_string()),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn lookup<'a>(vars: &'a [(&'a str, &'a str)]) -> impl Fn(&str) -> Option<String> + 'a {
        move |key| {
            vars.iter()
                .find(|(k, _)| *k == key)
                .map(|(_, v)| v.to_string())
        }
    }

    #[test]
    fn test_defaults() {
        let cfg = Config::from_map(lookup(&[]));
        assert_eq!(cfg.port, 8081);
        assert_eq!(cfg.ntfy_base_url, "https://ntfy.sh");
        assert_eq!(cfg.ntfy_topic, "belgrade-os");
    }

    #[test]
    fn test_from_map_port_and_topic() {
        let cfg = Config::from_map(lookup(&[("PORT", "9090"), ("NTFY_TOPIC", "my-topic")]));
        assert_eq!(cfg.port, 9090);
        assert_eq!(cfg.ntfy_base_url, "https://ntfy.sh");
        assert_eq!(cfg.ntfy_topic, "my-topic");
    }

    #[test]
    fn test_ntfy_base_url() {
        let cfg = Config::from_map(lookup(&[("NTFY_BASE_URL", "http://localhost:8088")]));
        assert_eq!(cfg.ntfy_base_url, "http://localhost:8088");
        assert_eq!(cfg.port, 8081);
    }
}
