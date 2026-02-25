use super::traits::{Channel, ChannelMessage, SendMessage};
use async_trait::async_trait;
use axum::{
    extract::{
        ws::{Message, WebSocket},
        Query, State, WebSocketUpgrade,
    },
    http::{HeaderMap, StatusCode},
    response::IntoResponse,
    routing::get,
    Router,
};
use futures_util::{SinkExt, StreamExt};
use serde::Deserialize;
use std::net::SocketAddr;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};
use tokio::sync::{broadcast, mpsc};

use crate::config::schema::OneBotConfig;

#[derive(Clone)]
struct OneBotServerState {
    config: OneBotConfig,
    outbound: broadcast::Sender<String>,
    inbound: mpsc::Sender<ChannelMessage>,
    connections: Arc<AtomicUsize>,
}

#[derive(Deserialize)]
struct OneBotQuery {
    access_token: Option<String>,
    #[serde(rename = "accessToken")]
    access_token_camel: Option<String>,
    token: Option<String>,
}

struct ConnectionGuard {
    connections: Arc<AtomicUsize>,
}

impl Drop for ConnectionGuard {
    fn drop(&mut self) {
        self.connections.fetch_sub(1, Ordering::SeqCst);
    }
}

pub struct OneBotChannel {
    config: OneBotConfig,
    outbound: broadcast::Sender<String>,
    connections: Arc<AtomicUsize>,
}

impl OneBotChannel {
    pub fn new(config: OneBotConfig) -> Self {
        let (outbound, _) = broadcast::channel(128);
        Self {
            config,
            outbound,
            connections: Arc::new(AtomicUsize::new(0)),
        }
    }
}

#[async_trait]
impl Channel for OneBotChannel {
    fn name(&self) -> &str {
        "onebot"
    }

    async fn send(&self, message: &SendMessage) -> anyhow::Result<()> {
        if self.connections.load(Ordering::SeqCst) == 0 {
            anyhow::bail!("OneBot not connected");
        }

        let payload = build_send_payload(&self.config, message)?;
        let text = payload.to_string();
        let _ = self.outbound.send(text);
        Ok(())
    }

    async fn listen(&self, tx: mpsc::Sender<ChannelMessage>) -> anyhow::Result<()> {
        let addr: SocketAddr =
            format!("{}:{}", self.config.listen_host, self.config.listen_port).parse()?;
        let listener = tokio::net::TcpListener::bind(addr).await?;

        tracing::info!(
            "OneBot WS server listening on ws://{}:{}{}",
            self.config.listen_host,
            self.config.listen_port,
            self.config.ws_path
        );

        let state = OneBotServerState {
            config: self.config.clone(),
            outbound: self.outbound.clone(),
            inbound: tx,
            connections: Arc::clone(&self.connections),
        };

        serve_onebot(listener, state).await?;
        Ok(())
    }

    async fn health_check(&self) -> bool {
        self.connections.load(Ordering::SeqCst) > 0
    }
}

async fn serve_onebot(listener: tokio::net::TcpListener, state: OneBotServerState) -> anyhow::Result<()> {
    let mut app = Router::new().route(&state.config.ws_path, get(handle_onebot_ws));

    if !state.config.ws_path.ends_with('/') {
        let alt_path = format!("{}/", state.config.ws_path);
        app = app.route(&alt_path, get(handle_onebot_ws));
    }

    let app = app.with_state(state);
    axum::serve(listener, app.into_make_service()).await?;
    Ok(())
}

async fn handle_onebot_ws(
    State(state): State<OneBotServerState>,
    Query(params): Query<OneBotQuery>,
    headers: HeaderMap,
    ws: WebSocketUpgrade,
) -> impl IntoResponse {
    if let Some(expected) = state.config.access_token.as_deref() {
        let token = extract_access_token(&headers, &params);
        match token {
            Some(token) if token == expected => {}
            Some(_) => {
                tracing::warn!("OneBot WS unauthorized connection attempt (invalid access token)");
                return (
                    StatusCode::UNAUTHORIZED,
                    "Unauthorized — invalid access token",
                )
                    .into_response();
            }
            None => {
                tracing::warn!("OneBot WS unauthorized connection attempt (missing access token)");
                return (
                    StatusCode::UNAUTHORIZED,
                    "Unauthorized — missing access token",
                )
                    .into_response();
            }
        }
    }

    ws.on_upgrade(move |socket| handle_socket(socket, state))
        .into_response()
}

async fn handle_socket(socket: WebSocket, state: OneBotServerState) {
    state.connections.fetch_add(1, Ordering::SeqCst);
    tracing::info!(
        "OneBot WS connected (connections={})",
        state.connections.load(Ordering::SeqCst)
    );
    let _guard = ConnectionGuard {
        connections: Arc::clone(&state.connections),
    };

    let (mut sender, mut receiver) = socket.split();
    let mut outbound = state.outbound.subscribe();
    let outbound_task = tokio::spawn(async move {
        while let Ok(msg) = outbound.recv().await {
            if sender.send(Message::Text(msg.into())).await.is_err() {
                break;
            }
        }
    });

    while let Some(Ok(msg)) = receiver.next().await {
        let text = match msg {
            Message::Text(text) => text,
            Message::Close(_) => break,
            _ => continue,
        };

        let value: serde_json::Value = match serde_json::from_str(text.as_str()) {
            Ok(v) => v,
            Err(_) => continue,
        };

        if let Some(channel_msg) = parse_onebot_event(&value, &state.config) {
            if state.inbound.send(channel_msg).await.is_err() {
                break;
            }
        }
    }

    outbound_task.abort();
    tracing::info!(
        "OneBot WS disconnected (connections={})",
        state.connections.load(Ordering::SeqCst)
    );
}

fn extract_access_token(headers: &HeaderMap, params: &OneBotQuery) -> Option<String> {
    if let Some(token) = params
        .access_token
        .as_ref()
        .or(params.access_token_camel.as_ref())
        .or(params.token.as_ref())
    {
        let token = strip_wrapping_quotes(token);
        if !token.is_empty() && !token.chars().all(|c| c.is_whitespace()) {
            return Some(token.to_string());
        }
    }

    for header_name in ["x-access-token", "x-token"] {
        if let Some(token) = headers.get(header_name).and_then(|v| v.to_str().ok()) {
            let token = strip_wrapping_quotes(token);
            if !token.is_empty() && !token.chars().all(|c| c.is_whitespace()) {
                return Some(token.to_string());
            }
        }
    }

    let auth = headers
        .get(axum::http::header::AUTHORIZATION)
        .and_then(|v| v.to_str().ok())?;

    let auth = auth.trim();
    if auth == "Bearer" || auth == "Token" {
        return None;
    }
    if let Some(rest) = auth.strip_prefix("Bearer ") {
        let token = strip_wrapping_quotes(rest);
        if !token.is_empty() && !token.chars().all(|c| c.is_whitespace()) {
            return Some(token.to_string());
        }
        return None;
    }
    if let Some(rest) = auth.strip_prefix("Token ") {
        let token = strip_wrapping_quotes(rest);
        if !token.is_empty() && !token.chars().all(|c| c.is_whitespace()) {
            return Some(token.to_string());
        }
        return None;
    }
    if !auth.is_empty() {
        return Some(auth.to_string());
    }

    None
}

fn strip_wrapping_quotes(value: &str) -> &str {
    let value = value.trim();
    if value.len() >= 2 {
        if let Some(stripped) = value.strip_prefix('"').and_then(|v| v.strip_suffix('"')) {
            return stripped;
        }
        if let Some(stripped) = value.strip_prefix('\'').and_then(|v| v.strip_suffix('\'')) {
            return stripped;
        }
    }
    value
}

fn parse_onebot_event(value: &serde_json::Value, config: &OneBotConfig) -> Option<ChannelMessage> {
    if value.get("post_type")?.as_str()? != "message" {
        return None;
    }

    let message_type = value.get("message_type")?.as_str()?;
    let user_id = parse_id(value.get("user_id"))?;
    let self_id = parse_id(value.get("self_id"));

    let is_group = message_type == "group";
    if is_group && !config.enable_group {
        return None;
    }
    if !is_group && !config.enable_private {
        return None;
    }

    if !is_allowed(&config.allowed_users, &user_id) {
        return None;
    }

    let group_id = if is_group {
        let group_id = parse_id(value.get("group_id"))?;
        if !is_allowed(&config.allowed_groups, &group_id) {
            return None;
        }
        Some(group_id)
    } else {
        None
    };

    let (content, mentioned) = parse_message_content(value, config, self_id.as_deref());
    if content.trim().is_empty() {
        return None;
    }

    if is_group && config.require_mention_in_group && !mentioned {
        return None;
    }

    let message_id = value
        .get("message_id")
        .and_then(|v| v.as_i64().map(|id| id.to_string()))
        .or_else(|| {
            value
                .get("message_id")
                .and_then(|v| v.as_str().map(|id| id.to_string()))
        })
        .unwrap_or_else(|| uuid::Uuid::new_v4().to_string());

    let timestamp = value
        .get("time")
        .and_then(|v| v.as_i64())
        .and_then(|t| u64::try_from(t).ok())
        .unwrap_or_else(|| {
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap_or_default()
                .as_secs()
        });

    let sender = if let Some(group_id) = group_id.as_ref() {
        format!("group:{group_id}:user:{user_id}")
    } else {
        format!("user:{user_id}")
    };
    let reply_target = if let Some(group_id) = group_id.as_ref() {
        format!("group:{group_id}")
    } else {
        format!("user:{user_id}")
    };

    Some(ChannelMessage {
        id: format!("onebot_{message_id}"),
        sender,
        reply_target,
        content,
        channel: "onebot".to_string(),
        timestamp,
        thread_ts: None,
    })
}

fn parse_message_content(
    value: &serde_json::Value,
    config: &OneBotConfig,
    self_id: Option<&str>,
) -> (String, bool) {
    if let Some(message) = value.get("message") {
        if let Some(text) = message.as_str() {
            return (
                text.to_string(),
                self_id
                    .map(|id| text.contains(&format!("qq={id}")) || text.contains("@all"))
                    .unwrap_or(false),
            );
        }

        if let Some(array) = message.as_array() {
            return parse_segments(array, config, self_id);
        }
    }

    if let Some(raw) = value.get("raw_message").and_then(|v| v.as_str()) {
        return (
            raw.to_string(),
            self_id
                .map(|id| raw.contains(&format!("qq={id}")) || raw.contains("@all"))
                .unwrap_or(false),
        );
    }

    (String::new(), false)
}

fn parse_segments(
    segments: &[serde_json::Value],
    config: &OneBotConfig,
    self_id: Option<&str>,
) -> (String, bool) {
    let mut parts = Vec::new();
    let mut mentioned = false;

    for seg in segments {
        let seg_type = seg.get("type").and_then(|v| v.as_str()).unwrap_or("");
        let data = seg.get("data").and_then(|v| v.as_object());

        match seg_type {
            "text" => {
                if let Some(text) = data.and_then(|d| d.get("text")).and_then(|v| v.as_str()) {
                    parts.push(text.to_string());
                }
            }
            "at" => {
                if let Some(qq) = data.and_then(|d| d.get("qq")).and_then(|v| v.as_str()) {
                    if qq == "all" {
                        mentioned = true;
                    } else if let Some(self_id) = self_id {
                        if qq == self_id {
                            mentioned = true;
                        }
                    }
                }
            }
            "image" => {
                if config.map_images_to_markers {
                    let target = data
                        .and_then(|d| {
                            d.get("url")
                                .or_else(|| d.get("file"))
                                .or_else(|| d.get("id"))
                        })
                        .and_then(|v| v.as_str())
                        .unwrap_or("");
                    if !target.is_empty() {
                        parts.push(format!("[IMAGE:{target}]"));
                    }
                }
            }
            "file" => {
                let name = data
                    .and_then(|d| d.get("name"))
                    .and_then(|v| v.as_str())
                    .unwrap_or("");
                let file_id = data
                    .and_then(|d| d.get("file_id").or_else(|| d.get("id")))
                    .and_then(|v| v.as_str())
                    .unwrap_or("");
                if !name.is_empty() || !file_id.is_empty() {
                    parts.push(format!("[FILE:name={name},id={file_id}]"));
                }
            }
            "record" => {
                parts.push("[AUDIO]".to_string());
            }
            "video" => {
                parts.push("[VIDEO]".to_string());
            }
            "reply" => {
                if let Some(msg_id) = data.and_then(|d| d.get("id")).and_then(|v| v.as_str()) {
                    parts.push(format!("(reply to #{msg_id})"));
                }
            }
            _ => {}
        }
    }

    (parts.join(""), mentioned)
}

fn parse_id(value: Option<&serde_json::Value>) -> Option<String> {
    let value = value?;
    if let Some(id) = value.as_i64() {
        return Some(id.to_string());
    }
    value.as_str().map(|id| id.to_string())
}

fn is_allowed(allowlist: &[String], value: &str) -> bool {
    if allowlist.is_empty() {
        return false;
    }
    if allowlist.iter().any(|v| v == "*") {
        return true;
    }
    allowlist.iter().any(|v| v == value)
}

fn build_send_payload(
    config: &OneBotConfig,
    message: &SendMessage,
) -> anyhow::Result<serde_json::Value> {
    let target = parse_recipient(&message.recipient).ok_or_else(|| {
        anyhow::anyhow!(
            "OneBot recipient must be user:<id> or group:<id> (got {})",
            message.recipient
        )
    })?;

    let body = build_message_payload(&message.content, config.expect_message_array);
    let echo = uuid::Uuid::new_v4().to_string();

    let payload = match target {
        OneBotTarget::Private(user_id) => serde_json::json!({
            "action": "send_private_msg",
            "params": {
                "user_id": parse_id_value(&user_id),
                "message": body,
            },
            "echo": echo,
        }),
        OneBotTarget::Group(group_id) => serde_json::json!({
            "action": "send_group_msg",
            "params": {
                "group_id": parse_id_value(&group_id),
                "message": body,
            },
            "echo": echo,
        }),
    };

    Ok(payload)
}

fn build_message_payload(content: &str, as_array: bool) -> serde_json::Value {
    if as_array {
        serde_json::json!([{ "type": "text", "data": { "text": content } }])
    } else {
        serde_json::json!(content)
    }
}

fn parse_id_value(id: &str) -> serde_json::Value {
    if let Ok(parsed) = id.parse::<i64>() {
        serde_json::json!(parsed)
    } else {
        serde_json::json!(id)
    }
}

enum OneBotTarget {
    Private(String),
    Group(String),
}

fn parse_recipient(recipient: &str) -> Option<OneBotTarget> {
    if let Some(id) = recipient.strip_prefix("user:") {
        return Some(OneBotTarget::Private(id.to_string()));
    }
    if let Some(id) = recipient.strip_prefix("group:") {
        return Some(OneBotTarget::Group(id.to_string()));
    }
    if recipient.chars().all(|c| c.is_ascii_digit()) {
        return Some(OneBotTarget::Private(recipient.to_string()));
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::agent::dispatcher::NativeToolDispatcher;
    use crate::agent::Agent;
    use crate::config::MemoryConfig;
    use crate::memory;
    use crate::observability::NoopObserver;
    use crate::providers::compatible::{AuthStyle, OpenAiCompatibleProvider};
    use futures_util::{SinkExt, StreamExt};
    use serde_json::Value;
    use std::sync::Arc;
    use tokio::process::Command;

    async fn wait_http_ok(url: &str) -> bool {
        let client = reqwest::Client::new();
        for _ in 0..50 {
            if let Ok(resp) = client.get(url).send().await {
                if resp.status().is_success() {
                    return true;
                }
            }
            tokio::time::sleep(std::time::Duration::from_millis(100)).await;
        }
        false
    }

    #[tokio::test]
    async fn onebot_closed_loop_to_pool_router_smoke() {
        let onebot_listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let onebot_port = onebot_listener.local_addr().unwrap().port();

        let router_listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let router_port = router_listener.local_addr().unwrap().port();
        drop(router_listener);

        let router_key = "test-router-key";
        let python_dir = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("python");
        let mock_cfg = r#"{
            "GLM-4-Flash": {"mode":"success","content":"router-ok"},
            "GLM-4-Flash-250414": {"mode":"success","content":"router-ok"},
            "GLM-Z1-Flash": {"mode":"success","content":"router-ok"},
            "GLM-4.7-Flash": {"mode":"success","content":"router-ok"}
        }"#;

        let mut child = Command::new("python")
            .arg("-m")
            .arg("pool_router")
            .current_dir(&python_dir)
            .env("FREEPOOL_HOST", "127.0.0.1")
            .env("FREEPOOL_PORT", router_port.to_string())
            .env("FREEPOOL_ROUTER_API_KEY", router_key)
            .env("FREEPOOL_GLM_KEYS", "dummy-key")
            .env("FREEPOOL_PROVIDER_MODE", "mock")
            .env("FREEPOOL_MOCK_MODEL_CONFIG", mock_cfg)
            .env("FREEPOOL_CLASSIFIER_MODE", "stub")
            .env("FREEPOOL_DRY_RUN", "1")
            .spawn()
            .unwrap();

        let healthz = format!("http://127.0.0.1:{router_port}/healthz");
        assert!(wait_http_ok(&healthz).await);

        let config = OneBotConfig {
            listen_host: "127.0.0.1".to_string(),
            listen_port: onebot_port,
            ws_path: "/onebot".to_string(),
            access_token: None,
            enable_private: true,
            enable_group: false,
            require_mention_in_group: false,
            allowed_users: vec!["*".to_string()],
            allowed_groups: vec!["*".to_string()],
            expect_message_array: true,
            map_images_to_markers: true,
        };
        let channel = OneBotChannel::new(config.clone());
        let (tx, mut rx) = mpsc::channel(8);
        let state = OneBotServerState {
            config,
            outbound: channel.outbound.clone(),
            inbound: tx,
            connections: Arc::clone(&channel.connections),
        };
        let server_task = tokio::spawn(async move { serve_onebot(onebot_listener, state).await });

        let url = format!("ws://127.0.0.1:{onebot_port}/onebot");
        let (mut ws, _) = tokio_tungstenite::connect_async(url).await.unwrap();

        let event = serde_json::json!({
            "post_type": "message",
            "message_type": "private",
            "user_id": 123,
            "self_id": 999,
            "message_id": 1,
            "time": 1700000000,
            "message": [{"type":"text","data":{"text":"hi"}}]
        });
        ws.send(tokio_tungstenite::tungstenite::Message::Text(
            event.to_string().into(),
        ))
            .await
            .unwrap();

        let incoming = rx.recv().await.unwrap();
        assert_eq!(incoming.reply_target, "user:123");
        assert_eq!(incoming.content, "hi");

        let provider = OpenAiCompatibleProvider::new(
            "router",
            &format!("http://127.0.0.1:{router_port}/v1"),
            Some(router_key),
            AuthStyle::Bearer,
        );
        let mem_cfg = MemoryConfig {
            backend: "none".to_string(),
            ..MemoryConfig::default()
        };
        let mem = Arc::from(memory::create_memory(&mem_cfg, &std::env::temp_dir(), None).unwrap());
        let mut agent = Agent::builder()
            .provider(Box::new(provider))
            .tools(vec![])
            .memory(mem)
            .observer(Arc::new(NoopObserver))
            .tool_dispatcher(Box::new(NativeToolDispatcher))
            .workspace_dir(std::env::temp_dir())
            .model_name("auto".to_string())
            .temperature(0.0)
            .build()
            .unwrap();
        let reply = agent.turn(&incoming.content).await.unwrap();
        channel
            .send(&SendMessage::new(&reply, &incoming.reply_target))
            .await
            .unwrap();

        let mut got = None;
        for _ in 0..20 {
            let next = tokio::time::timeout(std::time::Duration::from_secs(1), ws.next()).await;
            if let Ok(Some(Ok(msg))) = next {
                if let tokio_tungstenite::tungstenite::Message::Text(text) = msg {
                    got = Some(text);
                    break;
                }
            }
        }
        let payload = got.unwrap();
        let v: Value = serde_json::from_str(&payload).unwrap();
        assert_eq!(v.get("action").and_then(|x| x.as_str()), Some("send_private_msg"));
        let sent = v
            .get("params")
            .and_then(|p| p.get("message"))
            .and_then(|m| m.as_array())
            .and_then(|a| a.first())
            .and_then(|x| x.get("data"))
            .and_then(|d| d.get("text"))
            .and_then(|t| t.as_str())
            .unwrap_or("");
        assert!(sent.starts_with("[DRY_RUN]"));

        server_task.abort();
        let _ = child.kill().await;
    }
}
