use super::traits::{Memory, MemoryCategory, MemoryEntry};
use crate::config::schema::Mem1BridgeConfig;
use anyhow::Context;
use async_trait::async_trait;
use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};
use tokio::sync::OnceCell;

pub struct Mem1BridgeMemory {
    base: Box<dyn Memory>,
    cfg: Mem1BridgeConfig,
    workspace_dir: PathBuf,
    client: reqwest::Client,
    ingest_once: OnceCell<()>,
}

impl Mem1BridgeMemory {
    pub fn new(base: Box<dyn Memory>, cfg: Mem1BridgeConfig, workspace_dir: PathBuf) -> Self {
        let client = crate::config::build_runtime_proxy_client("memory.mem1");
        Self {
            base,
            cfg,
            workspace_dir,
            client,
            ingest_once: OnceCell::new(),
        }
    }

    fn base_url(&self) -> Option<String> {
        self.cfg
            .base_url
            .as_deref()
            .map(str::trim)
            .filter(|v| !v.is_empty())
            .map(|v| v.trim_end_matches('/').to_string())
    }

    fn resolve_path(&self, raw: &str) -> PathBuf {
        let p = Path::new(raw);
        if p.is_absolute() {
            return p.to_path_buf();
        }
        self.workspace_dir.join(p)
    }

    async fn ensure_ingested(&self) -> anyhow::Result<()> {
        self.ingest_once
            .get_or_try_init(|| async {
                if !self.cfg.ingest_on_startup {
                    return Ok::<(), anyhow::Error>(());
                }
                let Some(base_url) = self.base_url() else {
                    return Ok::<(), anyhow::Error>(());
                };
                let paths = self.cfg.ingest_paths.clone();
                if paths.is_empty() {
                    return Ok::<(), anyhow::Error>(());
                }

                for raw in paths {
                    let raw = raw.trim();
                    if raw.is_empty() {
                        continue;
                    }
                    let path = self.resolve_path(raw);
                    let body = serde_json::json!({
                        "source_type": "file",
                        "source_path": path.to_string_lossy(),
                        "user_id": self.cfg.user_id.clone(),
                        "agent_id": self.cfg.agent_id.clone(),
                        "run_id": null,
                        "incremental": true
                    });
                    let url = format!("{base_url}/mem1/ingest");
                    let timeout = std::time::Duration::from_secs(self.cfg.timeout_secs);
                    let resp = self
                        .client
                        .post(url)
                        .timeout(timeout)
                        .json(&body)
                        .send()
                        .await;
                    match resp {
                        Ok(r) if r.status().is_success() => {}
                        Ok(r) => {
                            let status = r.status();
                            let text = r.text().await.unwrap_or_default();
                            tracing::warn!(
                                "mem1 ingest failed (status={status}) for path={}: {}",
                                path.display(),
                                text
                            );
                        }
                        Err(e) => {
                            tracing::warn!("mem1 ingest request failed for path={}: {e}", path.display());
                        }
                    }
                }
                Ok::<(), anyhow::Error>(())
            })
            .await?;
        Ok(())
    }

    async fn mem1_search(
        &self,
        query: &str,
        limit: usize,
        session_id: Option<&str>,
    ) -> anyhow::Result<Vec<MemoryEntry>> {
        let Some(base_url) = self.base_url() else {
            return Ok(vec![]);
        };

        let run_id = session_id.map(|s| s.to_string());
        let body = Mem1SearchRequest {
            query: query.to_string(),
            user_id: self.cfg.user_id.clone(),
            agent_id: self.cfg.agent_id.clone(),
            run_id,
            limit,
        };
        let url = format!("{base_url}/search");
        let timeout = std::time::Duration::from_secs(self.cfg.timeout_secs);
        let resp = self
            .client
            .post(url)
            .timeout(timeout)
            .json(&body)
            .send()
            .await
            .context("mem1 /search request failed")?;
        if !resp.status().is_success() {
            return Ok(vec![]);
        }
        let hits: Vec<Mem1SearchHit> = resp.json().await.unwrap_or_default();

        let mut out: Vec<MemoryEntry> = vec![];
        for h in hits {
            let key = h
                .metadata
                .as_ref()
                .and_then(|m| m.get("source_path"))
                .and_then(|v| v.as_str())
                .map(|v| v.to_string())
                .unwrap_or_else(|| format!("mem1:{}", h.id));
            out.push(MemoryEntry {
                id: h.id,
                key,
                content: h.memory,
                category: MemoryCategory::Conversation,
                timestamp: h.created_at.unwrap_or_else(|| chrono::Utc::now().to_rfc3339()),
                session_id: session_id.map(|s| s.to_string()),
                score: Some(h.score),
            });
        }
        Ok(out)
    }

    async fn mem1_store(
        &self,
        key: &str,
        content: &str,
        category: &MemoryCategory,
        session_id: Option<&str>,
    ) -> anyhow::Result<()> {
        let Some(base_url) = self.base_url() else {
            return Ok(());
        };
        let body = serde_json::json!({
            "messages": [
                {"role": "user", "content": content}
            ],
            "user_id": self.cfg.user_id.clone(),
            "agent_id": self.cfg.agent_id.clone(),
            "run_id": session_id,
            "metadata": {
                "zeroclaw_key": key,
                "zeroclaw_category": category.to_string(),
                "source_type": "zeroclaw_autosave"
            }
        });
        let url = format!("{base_url}/memories");
        let timeout = std::time::Duration::from_secs(self.cfg.timeout_secs);
        let resp = self.client.post(url).timeout(timeout).json(&body).send().await;
        match resp {
            Ok(r) if r.status().is_success() => Ok(()),
            Ok(r) => {
                let status = r.status();
                let text = r.text().await.unwrap_or_default();
                anyhow::bail!("mem1 /memories failed (status={status}): {text}");
            }
            Err(e) => Err(anyhow::anyhow!(e)).context("mem1 /memories request failed"),
        }
    }
}

#[async_trait]
impl Memory for Mem1BridgeMemory {
    fn name(&self) -> &str {
        "mem1_bridge"
    }

    async fn store(
        &self,
        key: &str,
        content: &str,
        category: MemoryCategory,
        session_id: Option<&str>,
    ) -> anyhow::Result<()> {
        let base_res = self.base.store(key, content, category.clone(), session_id).await;
        if let Err(e) = &base_res {
            tracing::warn!("base memory store failed: {e}");
        }
        let _ = self.ensure_ingested().await;
        let mem1_res = self
            .mem1_store(key, content, &category, session_id)
            .await;
        if let Err(e) = mem1_res {
            tracing::warn!("mem1 store failed: {e}");
        }
        base_res
    }

    async fn recall(
        &self,
        query: &str,
        limit: usize,
        session_id: Option<&str>,
    ) -> anyhow::Result<Vec<MemoryEntry>> {
        let _ = self.ensure_ingested().await;
        let mut out = self.base.recall(query, limit, session_id).await.unwrap_or_default();

        let mem1_limit = self.cfg.recall_limit.min(limit.max(1));
        let mem1_entries = self
            .mem1_search(query, mem1_limit, session_id)
            .await;
        if let Ok(entries) = mem1_entries {
            for e in entries {
                if out.iter().any(|x| x.content == e.content) {
                    continue;
                }
                out.push(e);
            }
        }

        out.sort_by(|a, b| {
            let ascore = a.score.unwrap_or(f64::NEG_INFINITY);
            let bscore = b.score.unwrap_or(f64::NEG_INFINITY);
            bscore
                .partial_cmp(&ascore)
                .unwrap_or(std::cmp::Ordering::Equal)
        });
        if out.len() > limit {
            out.truncate(limit);
        }
        Ok(out)
    }

    async fn get(&self, key: &str) -> anyhow::Result<Option<MemoryEntry>> {
        self.base.get(key).await
    }

    async fn list(
        &self,
        category: Option<&MemoryCategory>,
        session_id: Option<&str>,
    ) -> anyhow::Result<Vec<MemoryEntry>> {
        self.base.list(category, session_id).await
    }

    async fn forget(&self, key: &str) -> anyhow::Result<bool> {
        self.base.forget(key).await
    }

    async fn count(&self) -> anyhow::Result<usize> {
        self.base.count().await
    }

    async fn health_check(&self) -> bool {
        let base_ok = self.base.health_check().await;
        let Some(base_url) = self.base_url() else {
            return base_ok;
        };
        let timeout = std::time::Duration::from_secs(self.cfg.timeout_secs);
        let url = format!("{base_url}/healthz");
        let resp = self.client.get(url).timeout(timeout).send().await;
        match resp {
            Ok(r) if r.status().is_success() => true,
            _ => base_ok,
        }
    }
}

#[derive(Debug, Clone, Serialize)]
struct Mem1SearchRequest {
    query: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    user_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    agent_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    run_id: Option<String>,
    limit: usize,
}

#[derive(Debug, Clone, Deserialize)]
struct Mem1SearchHit {
    id: String,
    memory: String,
    score: f64,
    #[serde(default)]
    metadata: Option<serde_json::Value>,
    #[serde(default)]
    created_at: Option<String>,
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::memory::none::NoneMemory;
    use wiremock::matchers::{method, path};
    use wiremock::{Mock, MockServer, ResponseTemplate};

    #[tokio::test]
    async fn mem1_bridge_calls_ingest_search_and_writeback() {
        let server = MockServer::start().await;

        Mock::given(method("GET"))
            .and(path("/healthz"))
            .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!({"ok": true})))
            .mount(&server)
            .await;

        Mock::given(method("POST"))
            .and(path("/mem1/ingest"))
            .respond_with(
                ResponseTemplate::new(200).set_body_json(serde_json::json!({
                    "message": "Ingest completed",
                    "chunks_added": 1,
                    "chunks_skipped": 0,
                    "errors": []
                })),
            )
            .expect(1)
            .mount(&server)
            .await;

        Mock::given(method("POST"))
            .and(path("/search"))
            .respond_with(
                ResponseTemplate::new(200).set_body_json(vec![serde_json::json!({
                    "id": "m_test",
                    "memory": "chunk: hello",
                    "score": 0.9,
                    "metadata": {"source_path": "docs/mem1_kb_small/demo.md"},
                    "created_at": "2026-02-26T00:00:00Z"
                })]),
            )
            .mount(&server)
            .await;

        Mock::given(method("POST"))
            .and(path("/memories"))
            .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!({"results": []})))
            .mount(&server)
            .await;

        let cfg = Mem1BridgeConfig {
            enabled: true,
            base_url: Some(server.uri()),
            user_id: Some("u_test".into()),
            agent_id: Some("a_test".into()),
            ingest_on_startup: true,
            ingest_paths: vec!["workspace/knowledge_base".into()],
            recall_limit: 5,
            timeout_secs: 5,
        };
        let base: Box<dyn Memory> = Box::new(NoneMemory::new());
        let mem = Mem1BridgeMemory::new(base, cfg, std::env::temp_dir());

        let hits = mem.recall("hello", 5, None).await.unwrap();
        assert!(!hits.is_empty());
        assert!(hits.iter().any(|e| e.content.contains("hello")));

        let hits2 = mem.recall("hello again", 5, None).await.unwrap();
        assert!(!hits2.is_empty());

        mem.store("user_msg", "hi", MemoryCategory::Conversation, None)
            .await
            .unwrap();
    }
}
