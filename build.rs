use std::path::Path;
use std::process::Command;

fn main() {
    let web_dir = Path::new("web");
    let dist_dir = web_dir.join("dist");
    let dist_index = dist_dir.join("index.html");
    let node_modules = web_dir.join("node_modules");

    println!("cargo:rerun-if-changed=web/src");
    println!("cargo:rerun-if-changed=web/index.html");
    println!("cargo:rerun-if-changed=web/package.json");
    println!("cargo:rerun-if-changed=web/vite.config.ts");
    println!("cargo:rerun-if-changed=web/tsconfig.json");
    println!("cargo:rerun-if-changed=web/tsconfig.app.json");

    let needs_frontend_assets = !dist_index.exists() || should_rebuild();

    if needs_frontend_assets {
        if pnpm_available() {
            if !node_modules.exists() {
                run_command("pnpm", &["install"], web_dir, "install dependencies");
            }
            run_command("pnpm", &["build"], web_dir, "build frontend");
        } else if !dist_index.exists() {
            ensure_fallback_index(&dist_dir, &dist_index);
        }
    }

    if !dist_index.exists() {
        panic!(
            "web/dist/index.html does not exist after build. \
             Ensure pnpm is installed and web/ contains a valid project."
        );
    }
}

fn should_rebuild() -> bool {
    let Ok(output) = Command::new("git")
        .args(["diff", "--name-only", "HEAD~1", "--", "web/"])
        .output()
    else {
        return false;
    };

    let stdout = String::from_utf8_lossy(&output.stdout);
    !stdout.trim().is_empty()
}

fn pnpm_available() -> bool {
    let program = if cfg!(windows) { "pnpm.cmd" } else { "pnpm" };
    Command::new(program)
        .arg("--version")
        .status()
        .map(|s| s.success())
        .unwrap_or(false)
}

fn ensure_fallback_index(dist_dir: &Path, dist_index: &Path) {
    let _ = std::fs::create_dir_all(dist_dir);

    let html = r#"<!doctype html>
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

    if let Err(err) = std::fs::write(dist_index, html) {
        panic!(
            "Failed to write fallback web/dist/index.html: {}. \
             Ensure web/dist is writable or install pnpm to build the frontend.",
            err
        );
    }
}

fn run_command(program: &str, args: &[&str], cwd: &Path, desc: &str) {
    let program = if cfg!(windows) {
        format!("{}.cmd", program)
    } else {
        program.to_string()
    };

    println!(
        "cargo:warning=Running: {} {} ({})",
        program,
        args.join(" "),
        desc
    );

    let result = Command::new(&program)
        .args(args)
        .current_dir(cwd)
        .status()
        .unwrap_or_else(|e| {
            panic!(
                "Failed to execute '{}' for {}: {}. \
                 Ensure {} is installed and in PATH.",
                program, desc, e, program
            )
        });

    if !result.success() {
        panic!("{} failed with exit code: {:?}", desc, result.code());
    }
}
