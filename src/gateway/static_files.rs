//! Static file serving for the embedded web dashboard.
//!
//! Uses `rust-embed` to bundle the `web/dist/` directory into the binary at compile time.

use axum::{
    http::{header, StatusCode, Uri},
    response::IntoResponse,
};
use rust_embed::Embed;

#[derive(Embed)]
#[folder = "web/dist/"]
struct WebAssets;

const FALLBACK_INDEX_HTML: &str = r#"<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width,initial-scale=1" />
    <title>ZeroClaw Dashboard</title>
  </head>
  <body>
    <h1>ZeroClaw Dashboard Unavailable</h1>
    <p>Frontend assets are not bundled in this build. Build the web UI to populate <code>web/dist</code>.</p>
  </body>
</html>
"#;

/// Serve static files from `/_app/*` path
pub async fn handle_static(uri: Uri) -> impl IntoResponse {
    let path = uri.path().strip_prefix("/_app/").unwrap_or(uri.path());

    serve_embedded_file(path)
}

/// SPA fallback: serve index.html for any non-API, non-static GET request
pub async fn handle_spa_fallback() -> impl IntoResponse {
    match WebAssets::get("index.html") {
        Some(content) => (
            StatusCode::OK,
            [
                (header::CONTENT_TYPE, "text/html; charset=utf-8".to_string()),
                (header::CACHE_CONTROL, "no-cache".to_string()),
            ],
            content.data.to_vec(),
        )
            .into_response(),
        None => (
            StatusCode::OK,
            [
                (header::CONTENT_TYPE, "text/html; charset=utf-8".to_string()),
                (header::CACHE_CONTROL, "no-cache".to_string()),
            ],
            FALLBACK_INDEX_HTML.as_bytes().to_vec(),
        )
            .into_response(),
    }
}

fn serve_embedded_file(path: &str) -> impl IntoResponse {
    match WebAssets::get(path) {
        Some(content) => {
            let mime = mime_guess::from_path(path)
                .first_or_octet_stream()
                .to_string();

            (
                StatusCode::OK,
                [
                    (header::CONTENT_TYPE, mime),
                    (
                        header::CACHE_CONTROL,
                        if path.contains("assets/") {
                            // Hashed filenames — immutable cache
                            "public, max-age=31536000, immutable".to_string()
                        } else {
                            // index.html etc — no cache
                            "no-cache".to_string()
                        },
                    ),
                ],
                content.data.to_vec(),
            )
                .into_response()
        }
        None => (StatusCode::NOT_FOUND, "Not found").into_response(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use http_body_util::BodyExt;

    #[tokio::test]
    async fn spa_fallback_serves_html_when_assets_missing() {
        let response = handle_spa_fallback().await.into_response();
        assert_eq!(response.status(), StatusCode::OK);

        let body = response.into_body().collect().await.unwrap().to_bytes();
        let body_str = String::from_utf8_lossy(&body);
        assert!(body_str.contains("ZeroClaw"));
    }
}
