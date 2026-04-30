pub mod belgrade_os {
    include!(concat!(env!("OUT_DIR"), "/belgrade_os.rs"));
}

pub mod config;
pub mod registry;
pub mod router;
pub mod store;

#[cfg(test)]
mod tests {
    use super::belgrade_os::{
        AppToolsRegistration, Task, ThoughtEvent, ThoughtEventType, Tool, ToolCall, ToolListResponse,
        ToolResult, WorkerLease,
    };

    #[test]
    fn test_task_fields() {
        let task = Task {
            task_id: "task-001".to_string(),
            user_id: "user-1".to_string(),
            prompt: "What's for dinner?".to_string(),
            created_at_ms: 1_700_000_000_000,
            trace_id: "trace-abc".to_string(),
        };
        assert_eq!(task.task_id, "task-001");
        assert_eq!(task.trace_id, "trace-abc");
    }

    #[test]
    fn test_tool_call_fields() {
        let call = ToolCall {
            call_id: "call-001".to_string(),
            task_id: "task-001".to_string(),
            tool_name: "shopping:add_item".to_string(),
            input_json: r#"{"item": "milk"}"#.to_string(),
            trace_id: "trace-abc".to_string(),
            user_id: "user-1".to_string(),
            tenant_id: "tenant-1".to_string(),
        };
        assert_eq!(call.tool_name, "shopping:add_item");
        assert_eq!(call.trace_id, "trace-abc");
    }

    #[test]
    fn test_tool_result_failure() {
        let result = ToolResult {
            call_id: "call-001".to_string(),
            task_id: "task-001".to_string(),
            success: false,
            output_json: String::new(),
            error: "app crashed".to_string(),
            duration_ms: 42,
            user_id: "user-1".to_string(),
            tenant_id: "tenant-1".to_string(),
        };
        assert!(!result.success);
        assert_eq!(result.error, "app crashed");
        assert_eq!(result.duration_ms, 42);
    }

    #[test]
    fn test_thought_event_done() {
        let ev = ThoughtEvent {
            task_id: "task-001".to_string(),
            user_id: "user-1".to_string(),
            r#type: ThoughtEventType::Done as i32,
            content: String::new(),
            trace_id: "trace-abc".to_string(),
        };
        assert_eq!(ev.r#type, ThoughtEventType::Done as i32);
        assert_eq!(ev.trace_id, "trace-abc");
    }

    #[test]
    fn test_thought_event_unspecified_default() {
        let ev = ThoughtEvent::default();
        assert_eq!(ev.r#type, ThoughtEventType::Unspecified as i32);
    }

    #[test]
    fn test_app_tools_registration() {
        let tool = Tool {
            name: "shopping:add_item".to_string(),
            description: "Add item to shopping list".to_string(),
            input_schema_json: r#"{"type":"object"}"#.to_string(),
            app_id: "shopping".to_string(),
        };
        let reg = AppToolsRegistration {
            app_id: "shopping".to_string(),
            tools: vec![tool],
        };
        assert_eq!(reg.tools.len(), 1);
        assert_eq!(reg.tools[0].name, "shopping:add_item");
    }

    #[test]
    fn test_tool_list_response() {
        let resp = ToolListResponse {
            tools: vec![
                Tool {
                    name: "shopping:add_item".to_string(),
                    description: String::new(),
                    input_schema_json: String::new(),
                    app_id: "shopping".to_string(),
                },
                Tool {
                    name: "meals:plan".to_string(),
                    description: String::new(),
                    input_schema_json: String::new(),
                    app_id: "meals".to_string(),
                },
            ],
        };
        assert_eq!(resp.tools.len(), 2);
    }

    #[test]
    fn test_worker_lease_expiry() {
        let lease = WorkerLease {
            worker_id: "worker-1".to_string(),
            task_id: "task-001".to_string(),
            call_id: "call-001".to_string(),
            leased_at_ms: 1_700_000_000_000,
            expires_at_ms: 1_700_000_060_000,
        };
        assert!(lease.expires_at_ms > lease.leased_at_ms);
    }
}
