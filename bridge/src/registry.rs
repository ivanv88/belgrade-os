use std::collections::HashMap;
use std::sync::RwLock;

#[derive(Clone, Debug)]
pub struct RegisteredTool {
    pub name: String,
    pub description: String,
    pub input_schema_json: String,
    pub app_id: String,
    pub callback_url: String,
}

pub struct ToolRegistration {
    pub name: String,
    pub description: String,
    pub input_schema_json: String,
}

pub struct ToolRegistry {
    tools: RwLock<HashMap<String, RegisteredTool>>,
    subscriptions: RwLock<HashMap<String, Vec<String>>>, // Topic -> Vec<AppId>
    // Maintained separately from RegisteredTool.callback_url to support event
    // fan-out by app_id without scanning the tools map.
    app_callbacks: RwLock<HashMap<String, String>>,      // AppId -> CallbackUrl
}

impl ToolRegistry {
    pub fn new() -> Self {
        Self {
            tools: RwLock::new(HashMap::new()),
            subscriptions: RwLock::new(HashMap::new()),
            app_callbacks: RwLock::new(HashMap::new()),
        }
    }

    pub fn register(&self, app_id: &str, callback_url: &str, tools: &[ToolRegistration]) {
        // NOTE: We acquire two different locks sequentially. This leaves a tiny window
        // where tools and callbacks might be out of sync, but since registration
        // is infrequent and authoritative, this is acceptable and avoids a more
        // complex state lock.
        {
            let mut callbacks = self.app_callbacks.write().expect("lock poisoned");
            callbacks.insert(app_id.to_string(), callback_url.to_string());
        }
        
        let mut map = self.tools.write().expect("lock poisoned");
        map.retain(|_, v| v.app_id != app_id);
        for t in tools {
            debug_assert!(
                t.name.starts_with(&format!("{}:", app_id)),
                "tool name {:?} must be namespaced as '{}:<name>'",
                t.name,
                app_id
            );
            map.insert(t.name.clone(), RegisteredTool {
                name: t.name.clone(),
                description: t.description.clone(),
                input_schema_json: t.input_schema_json.clone(),
                app_id: app_id.to_string(),
                callback_url: callback_url.to_string(),
            });
        }
    }

    pub fn subscribe(&self, app_id: &str, topics: Vec<String>) {
        let mut subs = self.subscriptions.write().expect("lock poisoned");
        
        // Remove app from all existing topics to ensure re-registration is authoritative
        for topic_list in subs.values_mut() {
            topic_list.retain(|id| id != app_id);
        }
        
        // Add to new topics
        for topic in topics {
            let entry = subs.entry(topic).or_default();
            if !entry.iter().any(|s| s == app_id) {
                entry.push(app_id.to_string());
            }
        }
        
        // Clean up empty topics
        subs.retain(|_, v| !v.is_empty());
    }

    pub fn get_subscribers(&self, topic: &str) -> Vec<(String, String)> {
        let subs = self.subscriptions.read().expect("lock poisoned");
        let callbacks = self.app_callbacks.read().expect("lock poisoned");
        
        subs.get(topic)
            .map(|app_ids| {
                app_ids.iter()
                    .filter_map(|id| {
                        callbacks.get(id).map(|url| (id.clone(), url.clone()))
                    })
                    .collect()
            })
            .unwrap_or_default()
    }

    pub fn get(&self, tool_name: &str) -> Option<RegisteredTool> {
        self.tools.read().expect("lock poisoned").get(tool_name).cloned()
    }

    pub fn list(&self) -> Vec<RegisteredTool> {
        let mut tools: Vec<RegisteredTool> = self.tools.read().expect("lock poisoned").values().cloned().collect();
        tools.sort_by(|a, b| a.name.cmp(&b.name));
        tools
    }

    pub fn unregister(&self, app_id: &str) {
        // Comprehensive cleanup across all registry maps
        {
            self.tools.write().expect("lock poisoned").retain(|_, v| v.app_id != app_id);
        }
        {
            self.app_callbacks.write().expect("lock poisoned").remove(app_id);
        }
        {
            let mut subs = self.subscriptions.write().expect("lock poisoned");
            for topic_list in subs.values_mut() {
                topic_list.retain(|id| id != app_id);
            }
            subs.retain(|_, v| !v.is_empty());
        }
    }
}

impl Default for ToolRegistry {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tool(name: &str) -> ToolRegistration {
        ToolRegistration {
            name: name.to_string(),
            description: "desc".to_string(),
            input_schema_json: "{}".to_string(),
        }
    }

    #[test]
    fn test_register_and_get() {
        let reg = ToolRegistry::new();
        reg.register("shopping", "http://app:8000", &[tool("shopping:add_item")]);
        let t = reg.get("shopping:add_item").unwrap();
        assert_eq!(t.name, "shopping:add_item");
        assert_eq!(t.app_id, "shopping");
        assert_eq!(t.callback_url, "http://app:8000");
    }

    #[test]
    fn test_subscribe_and_get_subscribers() {
        let reg = ToolRegistry::new();
        reg.register("app1", "http://app1:8000", &[]);
        reg.subscribe("app1", vec!["topic1".to_string()]);
        
        let subs = reg.get_subscribers("topic1");
        assert_eq!(subs.len(), 1);
        assert_eq!(subs[0].0, "app1");
        assert_eq!(subs[0].1, "http://app1:8000");
    }

    #[test]
    fn test_subscribe_authoritative_replaces_old() {
        let reg = ToolRegistry::new();
        reg.register("app1", "http://app1:8000", &[]);
        reg.subscribe("app1", vec!["topic1".to_string(), "topic2".to_string()]);
        reg.subscribe("app1", vec!["topic2".to_string()]);
        
        assert!(reg.get_subscribers("topic1").is_empty());
        assert_eq!(reg.get_subscribers("topic2").len(), 1);
    }

    #[test]
    fn test_unregister_cleans_all_maps() {
        let reg = ToolRegistry::new();
        reg.register("app1", "http://app1:8000", &[tool("app1:t1")]);
        reg.subscribe("app1", vec!["topic1".to_string()]);
        
        reg.unregister("app1");
        
        assert!(reg.get("app1:t1").is_none());
        assert!(reg.get_subscribers("topic1").is_empty());
        assert!(reg.app_callbacks.read().unwrap().get("app1").is_none());
    }

    #[test]
    fn test_get_unknown_returns_none() {
        let reg = ToolRegistry::new();
        assert!(reg.get("unknown:tool").is_none());
    }

    #[test]
    fn test_list_returns_all_tools() {
        let reg = ToolRegistry::new();
        reg.register("app1", "http://app1:8000", &[tool("app1:t1"), tool("app1:t2")]);
        assert_eq!(reg.list().len(), 2);
    }

    #[test]
    fn test_re_register_replaces_old_tools() {
        let reg = ToolRegistry::new();
        reg.register("shopping", "http://app:8000", &[tool("shopping:old")]);
        reg.register("shopping", "http://app:8000", &[tool("shopping:new")]);
        assert!(reg.get("shopping:old").is_none());
        assert!(reg.get("shopping:new").is_some());
    }

    #[test]
    fn test_re_register_does_not_affect_other_apps() {
        let reg = ToolRegistry::new();
        reg.register("app1", "http://app1:8000", &[tool("app1:t1")]);
        reg.register("app2", "http://app2:8000", &[tool("app2:t1")]);
        reg.register("app1", "http://app1:8000", &[tool("app1:t2")]);
        assert!(reg.get("app1:t1").is_none());
        assert!(reg.get("app1:t2").is_some());
        assert!(reg.get("app2:t1").is_some()); // app2 untouched
    }

    #[test]
    #[cfg(debug_assertions)]
    #[should_panic(expected = "must be namespaced")]
    fn test_register_rejects_unnamespaceed_tool() {
        let reg = ToolRegistry::new();
        reg.register("shopping", "http://app:8000", &[ToolRegistration {
            name: "add_item".to_string(), // missing "shopping:" prefix
            description: "".to_string(),
            input_schema_json: "{}".to_string(),
        }]);
    }

    #[test]
    fn test_unregister_removes_app_tools() {
        let reg = ToolRegistry::new();
        reg.register("shopping", "http://app:8000", &[tool("shopping:add_item")]);
        reg.register("meals", "http://meals:8000", &[tool("meals:plan")]);
        reg.unregister("shopping");
        assert!(reg.get("shopping:add_item").is_none());
        assert!(reg.get("meals:plan").is_some()); // meals untouched
    }

    #[test]
    fn test_list_is_sorted_by_name() {
        let reg = ToolRegistry::new();
        reg.register("app1", "http://app1:8000", &[tool("app1:z"), tool("app1:a")]);
        let tools = reg.list();
        let names: Vec<&str> = tools.iter().map(|t| t.name.as_str()).collect();
        assert_eq!(names, vec!["app1:a", "app1:z"]);
    }
}
