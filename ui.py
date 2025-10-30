import io
import os
import time
import zipfile
from pathlib import Path
import uuid
from functools import partial
import threading
import socket
from http.server import SimpleHTTPRequestHandler
from socketserver import TCPServer

import streamlit as st
from dotenv import load_dotenv
from streamlit import components
import base64
import json as _json
import urllib.parse
import shutil

# Ensure environment variables (e.g., API keys) are loaded
load_dotenv()

# Import the compiled agent and project tools
from agent.graph import agent  # type: ignore
from agent.tools import (
    init_project_root,
    get_project_root,
    delete_session_root,
    cleanup_stale_sessions,
    set_default_session_id,
)


PROJECT_DIR = Path.cwd() / "generated_project"


def get_preview_dir(session_id: str) -> Path:
    return Path(f"/tmp/solace/preview/{session_id}")


def materialize_preview(session_id: str, files_payload: dict[str, str]) -> Path:
    """Write the in-memory files to a temp preview directory and return the path."""
    preview_dir = get_preview_dir(session_id)
    # clean old
    if preview_dir.exists():
        try:
            shutil.rmtree(preview_dir)
        except Exception:
            pass
    preview_dir.mkdir(parents=True, exist_ok=True)
    for rel_path, content in files_payload.items():
        try:
            target = preview_dir / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        except Exception:
            # skip any problematic files
            continue
    return preview_dir


def clear_preview_dir(session_id: str) -> None:
    p = get_preview_dir(session_id)
    if p.exists():
        try:
            shutil.rmtree(p)
        except Exception:
            pass


# best-effort cleanup of stale sessions on app start
try:
    cleanup_stale_sessions(max_age_hours=6)
except Exception:
    pass

# On hard refresh, optionally restore from URL query params if provided by the browser bridge
params = st.query_params

def _first_param_value(v):
    try:
        # Streamlit may return list-like values in query params
        if isinstance(v, (list, tuple)):
            return v[0] if v else ""
        return v or ""
    except Exception:
        return ""
if (
    "ls" in params
    and _first_param_value(params.get("ls"))
    and "restored_once" not in st.session_state
):
    try:
        sid = _first_param_value(params.get("sid", ""))
        b64 = _first_param_value(params.get("ls", ""))
        raw = base64.b64decode(b64).decode("utf-8")
        payload = _json.loads(raw)
        if sid:
            st.session_state["session_id"] = sid
        st.session_state["project_files_payload"] = payload
        st.session_state["url_cleaned"] = False
        st.session_state["restored_once"] = True
    except Exception:
        pass
    # Defer URL cleanup to after Streamlit state is initialized

# If no in-memory payload and no URL restore present, auto-restore from LocalStorage (pick most recent)
if "project_files_payload" not in st.session_state and not ("ls" in params and params.get("ls")):
    auto_bridge = """
    <script>
    (function() {
      try {
        // Don't loop if params already present
        const urlParams = new URLSearchParams(window.location.search);
        if (urlParams.get('ls')) return;

        // Pick most recent by saved timestamp
        let chosenSid = null;
        let latestTs = -1;
        for (let i = 0; i < localStorage.length; i++) {
          const k = localStorage.key(i);
          if (!k) continue;
          if (k.startsWith('solace_saved_at:')) {
            const sid = k.substring('solace_saved_at:'.length);
            const ts = parseInt(localStorage.getItem(k) || '0', 10) || 0;
            if (ts > latestTs) { latestTs = ts; chosenSid = sid; }
          }
        }
        if (!chosenSid) {
          chosenSid = localStorage.getItem('solace_session_id') || null;
        }
        if (!chosenSid) {
          // fallback: first JSON-looking entry
          for (let i = 0; i < localStorage.length; i++) {
            const k = localStorage.key(i);
            if (!k) continue;
            try {
              const v = localStorage.getItem(k) || '';
              const obj = JSON.parse(v);
              if (obj && typeof obj === 'object') { chosenSid = k; break; }
            } catch (e) { /* ignore */ }
          }
        }
        if (!chosenSid) return; // nothing to restore
        const data = localStorage.getItem(chosenSid) || '{}';
        const b64 = btoa(unescape(encodeURIComponent(data)));
        const params = new URLSearchParams(window.location.search);
        params.set('sid', chosenSid);
        params.set('ls', b64);
        const next = window.location.pathname + '?' + params.toString();
        window.location.replace(next);
      } catch (e) {
        console.error('auto-restore failed', e);
      }
    })();
    </script>
    """
    components.v1.html(auto_bridge, height=0)



def zip_directory_to_bytes(directory: Path) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zipf:
        for file_path in directory.rglob("*"):
            if file_path.is_file():
                zipf.write(file_path, arcname=file_path.relative_to(directory))
    buffer.seek(0)
    return buffer.read()


def list_files_recursive(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    return [p for p in directory.rglob("*") if p.is_file()]


def render_file_preview(file_path: Path):
    try:
        content = file_path.read_text(encoding="utf-8")
    except Exception as e:
        st.error(f"Error reading file: {e}")
        return

    ext = file_path.suffix.lower()

    if ext in [".py", ".js", ".ts", ".json", ".css", ".html", ".md"]:
        # Display as syntax-highlighted code (never render HTML)
        lang = ext.replace(".", "")
        st.code(content, language=lang)
    else:
        # For other file types, show plain text
        st.text(content)


def run_generation(user_prompt: str, session_id: str):
    # Ensure session directory exists (agent also initializes)
    init_project_root(session_id)
    # Force all tool calls to route to this session
    try:
        set_default_session_id(session_id)
    except Exception:
        pass
    # Invoke the agent with session context
    result = agent.invoke({"user_prompt": user_prompt, "session_id": session_id}, {"recursion_limit": 100})
    return result


def read_all_session_files(session_id: str) -> dict[str, str]:
    """Read all files from the per-session temp directory and return a mapping of path->content."""
    root = get_project_root(session_id)
    data: dict[str, str] = {}
    if not root.exists():
        return data
    for p in root.rglob("*"):
        if p.is_file():
            rel = str(p.relative_to(root))
            try:
                data[rel] = p.read_text(encoding="utf-8")
            except Exception:
                # skip unreadable files
                continue
    return data


def send_to_local_storage(session_id: str, payload: dict[str, str]):
    """Emit a small HTML/JS component that stores payload JSON in localStorage under sessionId."""
    js = f"""
    <script>
      try {{
        const key = {session_id!r};
        const payload = {_json.dumps(payload)};
        localStorage.setItem(key, JSON.stringify(payload));
        // remember last session id
        localStorage.setItem('solace_session_id', key);
        // store a timestamp to help pick the most recent session
        try {{ localStorage.setItem('solace_saved_at:' + key, String(Date.now())); }} catch (e) {{}}
        window.parent.postMessage({{ type: 'solace-localstore-success', sessionId: key }}, '*');
      }} catch (e) {{
        window.parent.postMessage({{ type: 'solace-localstore-error', error: String(e) }}, '*');
      }}
    </script>
    """
    components.v1.html(js, height=0)


# --- Sandbox HTTP server for live app preview ---
class ThreadedTCPServer(TCPServer):
    allow_reuse_address = True


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def ensure_sandbox_server(directory: Path):
    if "sandbox_server" not in st.session_state:
        st.session_state["sandbox_server"] = {
            "thread": None,
            "port": None,
            "dir": None,
        }
    sv = st.session_state["sandbox_server"]
    if sv["thread"] and sv["thread"].is_alive() and sv["dir"] == str(directory):
        return sv["port"]

    # Start/restart server
    port = find_free_port()
    handler = partial(SimpleHTTPRequestHandler, directory=str(directory))

    httpd = ThreadedTCPServer(("127.0.0.1", port), handler)

    def serve():
        try:
            httpd.serve_forever()
        finally:
            httpd.server_close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()

    sv.update({"thread": thread, "port": port, "dir": str(directory)})
    return port


st.set_page_config(page_title="Solace", page_icon="🤖", layout="wide")
st.title("Solace UI")
st.write("Type a prompt, generate an app, preview files, and download.")

with st.sidebar:
    # Ensure a per-tab session id
    if "session_id" not in st.session_state:
        st.session_state["session_id"] = str(uuid.uuid4())
    session_id = st.session_state["session_id"]

    st.header("Generation")
    prompt = st.text_area(
        "Describe the app you want",
        placeholder="e.g., Create a simple calculator web app with +, -, ×, ÷",
        height=140,
    )
    generate_clicked = st.button("Generate", type="primary", use_container_width=True)

    st.divider()
    clear_clicked = st.button("Clear generated project", use_container_width=True)

    if clear_clicked:
        # also clear any preview directory
        try:
            clear_preview_dir(session_id)
        except Exception:
            pass
        if PROJECT_DIR.exists():
            # Remove files recursively
            for p in sorted(PROJECT_DIR.rglob("*"), reverse=True):
                try:
                    if p.is_file():
                        p.unlink()
                    elif p.is_dir():
                        p.rmdir()
                except Exception:
                    pass
        # Clear in-memory payload
        if "project_files_payload" in st.session_state:
            st.session_state.pop("project_files_payload", None)
        # Clear LocalStorage entries for this session in the browser
        components.v1.html(
            (
                """
                <script>
                try {
                  const sid = localStorage.getItem('solace_session_id') || %s;
                  if (sid) {
                    localStorage.removeItem(sid);
                    try { localStorage.removeItem('solace_saved_at:' + sid); } catch (e) {}
                  }
                  try { localStorage.removeItem('solace_session_id'); } catch (e) {}
                } catch (e) {}
                </script>
                """
                % (repr(session_id))
            ),
            height=0,
        )
        st.rerun()


if generate_clicked:
    if not prompt or not prompt.strip():
        st.error("Please enter a prompt before generating.")
    else:
        with st.status("Generating app...", expanded=True) as status:
            st.write("Invoking agent with your prompt...")
            try:
                _ = run_generation(prompt.strip(), session_id)
                # collect all generated files for this session
                files_payload = read_all_session_files(session_id)
                # send to localStorage in the browser
                send_to_local_storage(session_id, files_payload)
                # keep in-memory copy for immediate preview
                st.session_state["project_files_payload"] = files_payload
                # delete temp session directory on the server
                delete_session_root(session_id)
                status.update(label="Generation complete (stored in your browser)", state="complete")
            except Exception as e:
                status.update(label="Generation failed", state="error")
                st.exception(e)


left, right = st.columns([1, 2], gap="large")

with left:
    st.subheader("Project Files")
    files_payload = st.session_state.get("project_files_payload")
    if files_payload:
        file_names = sorted(files_payload.keys())
        for name in file_names:
            st.write(f"- {name}")
    else:
        files = list_files_recursive(PROJECT_DIR)
        if not files:
            st.info("No generated files yet. Enter a prompt and click Generate.")
        else:
            for p in files:
                st.write(f"- {p.relative_to(PROJECT_DIR)}")

        st.divider()
        zip_disabled = not PROJECT_DIR.exists() or not any(PROJECT_DIR.rglob("*"))
        if not zip_disabled:
            zip_bytes = zip_directory_to_bytes(PROJECT_DIR)
            st.download_button(
                label="Download project as ZIP",
                data=zip_bytes,
                file_name="generated_project.zip",
                mime="application/zip",
                use_container_width=True,
            )

with right:
    st.subheader("Preview")
    files_payload = st.session_state.get("project_files_payload")
    if files_payload:
        names = sorted(files_payload.keys())
        if not names:
            st.caption("Nothing to preview yet.")
        else:
            tab_code, tab_app = st.tabs(["Code Preview", "App Preview (Browser)"])

            with tab_code:
                selected = st.selectbox("Select a file", names, index=0, key="code_select_payload")
                st.code(files_payload.get(selected, ""), language=selected.split(".")[-1])

            with tab_app:
                try:
                    preview_dir = materialize_preview(session_id, files_payload)
                    port = ensure_sandbox_server(preview_dir)
                    nonce = str(int(time.time()))
                    st.markdown(
                        f"""
                        <div style="background:white; border-radius:8px; overflow:hidden;">
                            <iframe src="http://127.0.0.1:{port}?_={nonce}" width="100%" height="700" frameborder="0"></iframe>
                        </div>
                        """,
                        unsafe_allow_html=True
                    )
                except Exception as e:
                    st.error(f"Preview failed: {e}")
    else:
        files = list_files_recursive(PROJECT_DIR)
        if not files:
            st.caption("Nothing to preview yet.")
        else:
            tab_code, tab_app = st.tabs(["Code Preview", "App Preview (Sandbox)"])

            with tab_code:
                file_options = [str(p.relative_to(PROJECT_DIR)) for p in files]
                selected = st.selectbox("Select a file", file_options, index=0, key="code_select")
                render_file_preview(PROJECT_DIR / selected)

            with tab_app:
                index_html = PROJECT_DIR / "index.html"
                if not index_html.exists():
                    st.info("No index.html found. Generate a web app with an index.html to preview.")
                else:
                    port = ensure_sandbox_server(PROJECT_DIR)
                    # cache-buster to force reload on rerun
                    nonce = str(int(time.time()))
                    st.markdown(
                        f"""
                        <div style="background:white; border-radius:8px; overflow:hidden;">
                            <iframe src="http://127.0.0.1:{port}?_={nonce}" width="100%" height="700" frameborder="0"></iframe>
                        </div>
                        """,
                        unsafe_allow_html=True
                    )

# After UI render: clean URL once after a successful restore
params = st.query_params
if (
    "project_files_payload" in st.session_state
    and not st.session_state.get("url_cleaned")
    and "ls" in params
    and _first_param_value(params.get("ls"))
):
    components.v1.html(
        """
        <script>
        try { window.history.replaceState({}, document.title, window.location.pathname); } catch (e) {}
        </script>
        """,
        height=0,
    )
    st.session_state["url_cleaned"] = True


