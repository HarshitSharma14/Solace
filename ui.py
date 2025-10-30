import io
import os
import time
import zipfile
from pathlib import Path
from functools import partial
import threading
import socket
from http.server import SimpleHTTPRequestHandler
from socketserver import TCPServer

import streamlit as st
from dotenv import load_dotenv
from streamlit import components

# Ensure environment variables (e.g., API keys) are loaded
load_dotenv()

# Import the compiled agent and project tools
from agent.graph import agent  # type: ignore
from agent.tools import init_project_root


PROJECT_DIR = Path.cwd() / "generated_project"


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


def run_generation(user_prompt: str):
    # Make sure project directory exists
    init_project_root()
    # Invoke the agent with a generous recursion limit
    result = agent.invoke({"user_prompt": user_prompt}, {"recursion_limit": 100})
    return result


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
        st.rerun()


if generate_clicked:
    if not prompt or not prompt.strip():
        st.error("Please enter a prompt before generating.")
    else:
        with st.status("Generating app...", expanded=True) as status:
            st.write("Invoking agent with your prompt...")
            try:
                _ = run_generation(prompt.strip())
                status.update(label="Generation complete", state="complete")
            except Exception as e:
                status.update(label="Generation failed", state="error")
                st.exception(e)


left, right = st.columns([1, 2], gap="large")

with left:
    st.subheader("Project Files")
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


