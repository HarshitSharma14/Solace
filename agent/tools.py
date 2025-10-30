import pathlib
import subprocess
from typing import Tuple

from langchain_core.tools import tool

PROJECT_ROOT = pathlib.Path.cwd() / "generated_project"


def safe_path_for_project(path: str) -> pathlib.Path:
    p = (PROJECT_ROOT / path).resolve()
    if PROJECT_ROOT.resolve() not in p.parents and PROJECT_ROOT.resolve() != p.parent and PROJECT_ROOT.resolve() != p:
        raise ValueError("Attempt to write outside project root")
    return p


@tool
def write_file(path: str, content: str) -> str:
    """Writes content to a file at the specified path within the project root."""
    p = safe_path_for_project(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(content)
    return f"WROTE:{p}"


@tool
def read_file(path: str) -> str:
    """Reads content from a file at the specified path within the project root."""
    p = safe_path_for_project(path)
    if not p.exists():
        return ""
    with open(p, "r", encoding="utf-8") as f:
        return f.read()


@tool
def get_current_directory() -> str:
    """Returns the current working directory."""
    return str(PROJECT_ROOT)


@tool
def list_files(directory: str = ".") -> str:
    """Lists all files in the specified directory within the project root."""
    p = safe_path_for_project(directory)
    if not p.is_dir():
        return f"ERROR: {p} is not a directory"
    files = [str(f.relative_to(PROJECT_ROOT)) for f in p.glob("**/*") if f.is_file()]
    return "\n".join(files) if files else "No files found."

@tool
def run_cmd(cmd: str, cwd: str = None, timeout: int = 30) -> Tuple[int, str, str]:
    """Runs a shell command in the specified directory and returns the result."""
    cwd_dir = safe_path_for_project(cwd) if cwd else PROJECT_ROOT
    res = subprocess.run(cmd, shell=True, cwd=str(cwd_dir), capture_output=True, text=True, timeout=timeout)
    return res.returncode, res.stdout, res.stderr


def init_project_root():
    PROJECT_ROOT.mkdir(parents=True, exist_ok=True)
    return str(PROJECT_ROOT)


# --- Compatibility aliases for models that expect repo_browser.* tools ---

@tool("repo_browser.write_file")
def repo_browser_write_file(path: str, content: str) -> str:
    """Compatibility alias that writes a file inside the generated project root."""
    return write_file.run(path, content)


@tool("repo_browser.read_file")
def repo_browser_read_file(path: str) -> str:
    """Compatibility alias that reads a file from the generated project root."""
    return read_file.run(path)


@tool("repo_browser.list_files")
def repo_browser_list_files(directory: str = ".") -> str:
    """Compatibility alias that lists files relative to the generated project root."""
    return list_files.run(directory)


@tool("repo_browser.get_current_directory")
def repo_browser_get_current_directory() -> str:
    """Compatibility alias that returns the generated project root path."""
    return get_current_directory.run()


@tool("repo_browser.run_cmd")
def repo_browser_run_cmd(cmd: str, cwd: str = None, timeout: int = 30) -> Tuple[int, str, str]:
    """Compatibility alias that runs a command within the generated project root."""
    return run_cmd.run(cmd, cwd, timeout)


@tool("repo_browser.print_tree")
def repo_browser_print_tree(path: str = "", depth: int = 2) -> str:
    """Compatibility alias that prints a tree of files under the generated project root.

    Args:
        path: Subdirectory relative to the project root to list. Defaults to root.
        depth: Max depth of recursion. Defaults to 2.
    """
    base = safe_path_for_project(path) if path else PROJECT_ROOT
    if not base.exists():
        return f"Path not found: {base}"
    lines: list[str] = []

    def walk(p: pathlib.Path, level: int):
        if level > depth:
            return
        try:
            entries = sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
        except Exception:
            return
        for e in entries:
            rel = e.relative_to(PROJECT_ROOT)
            prefix = "  " * level + ("- " if level else "")
            lines.append(f"{prefix}{rel}/" if e.is_dir() else f"{prefix}{rel}")
            if e.is_dir():
                walk(e, level + 1)

    walk(base, 0)
    return "\n".join(lines) if lines else "(empty)"