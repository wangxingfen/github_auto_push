#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GitHub Auto —— 一个 AI 工具

自动分析指定文件夹中的项目，生成精美、可直接用于 GitHub 的 README.md，
并自动创建 GitHub 仓库、推送代码。

用法:
    python github_auto.py analyze <项目路径> [--lang zh|en] [--force] [--dry-run]
    python github_auto.py run     <项目根目录> [--private] [--skip-push] [--dry-run]
    python github_auto.py push    <项目路径> [--private] [--dry-run]

配置（config.json 或环境变量，环境变量优先）:
    LLM:   OPENAI_API_KEY / LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
    GitHub: GITHUB_TOKEN / GH_TOKEN
"""

from __future__ import annotations

import argparse
import base64
import html
import json
import os
import re
import shutil
import ssl
import string
import subprocess
import sys
import threading
import time
import webbrowser
from collections import Counter
from copy import deepcopy
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib import error, request
from urllib.parse import parse_qs, urlparse
from urllib.parse import quote

VERSION = "1.0.0"

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


# --------------------------------------------------------------------------
# 基础数据
# --------------------------------------------------------------------------

LANG_MAP = {
    ".py": "Python", ".pyw": "Python",
    ".js": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript", ".jsx": "JavaScript",
    ".ts": "TypeScript", ".tsx": "TypeScript",
    ".go": "Go",
    ".rs": "Rust",
    ".java": "Java", ".kt": "Kotlin", ".kts": "Kotlin",
    ".c": "C", ".h": "C/C++ Header", ".cpp": "C++", ".cc": "C++", ".cxx": "C++", ".hpp": "C++",
    ".cs": "C#",
    ".rb": "Ruby",
    ".php": "PHP",
    ".swift": "Swift",
    ".scala": "Scala",
    ".html": "HTML", ".htm": "HTML",
    ".css": "CSS", ".scss": "SCSS", ".sass": "Sass", ".less": "Less",
    ".vue": "Vue", ".svelte": "Svelte",
    ".sh": "Shell", ".bash": "Shell", ".zsh": "Shell",
    ".ps1": "PowerShell",
    ".sql": "SQL",
    ".r": "R", ".lua": "Lua", ".pl": "Perl", ".dart": "Dart",
    ".ex": "Elixir", ".exs": "Elixir", ".erl": "Erlang",
    ".hs": "Haskell", ".ml": "OCaml", ".zig": "Zig",
    ".groovy": "Groovy", ".tf": "Terraform", ".hcl": "HCL",
}

MANIFESTS = {
    "package.json", "pyproject.toml", "setup.py", "requirements.txt",
    "cargo.toml", "go.mod", "pom.xml", "build.gradle", "gemfile",
    "composer.json", "dockerfile", "makefile",
}

SKIP_DIRS = {
    ".git", "node_modules", "venv", ".venv", "__pycache__", "dist", "build",
    "target", ".idea", ".vscode", ".next", ".nuxt", ".output", "vendor",
    "Pods", ".tox", "site-packages", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", ".cache", "coverage", ".eggs", "env", ".env",
    ".turbo", ".parcel-cache", ".gradle", ".svn", ".hg", "logs",
}

BINARY_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp", ".bmp",
    ".pdf", ".zip", ".gz", ".tar", ".7z", ".rar", ".xz", ".bz2",
    ".exe", ".dll", ".so", ".dylib", ".class", ".jar", ".pyc", ".pyo",
    ".whl", ".woff", ".woff2", ".ttf", ".otf", ".eot", ".mp3", ".mp4",
    ".avi", ".mov", ".mkv", ".db", ".sqlite", ".sqlite3", ".lock",
}

TEXT_EXTS = {".md", ".markdown", ".rst", ".json", ".yaml", ".yml", ".toml",
             ".ini", ".cfg", ".conf", ".txt", ".csv", ".tsv", ".xml"}

LICENSE_NAMES = {
    "mit": "MIT", "apache": "Apache-2.0", "gpl": "GPL", "lgpl": "LGPL",
    "bsd": "BSD", "mpl": "MPL-2.0", "agpl": "AGPL", "unlicense": "Unlicense",
    "isc": "ISC",
}


_LOGGER = None


def log(msg: str, level: str = "INFO") -> None:
    if _LOGGER is not None:
        _LOGGER(msg, level)
        return
    tag = {
        "INFO": "[*]",
        "WARN": "[!]",
        "OK": "[+]",
        "ERR": "[-]",
    }.get(level, "[*]")
    print(f"{tag} {msg}", flush=True)


def set_logger(fn) -> None:
    """切换全局日志输出目标（Web 任务运行时重定向到任务日志）。"""
    global _LOGGER
    _LOGGER = fn


class ToolError(Exception):
    """工具运行过程中的可预期错误。"""


# --------------------------------------------------------------------------
# 配置
# --------------------------------------------------------------------------

DEFAULT_CONFIG = {
    "llm": {
        "api_key": "",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
    },
    "github": {
        "token": "",
        "author_name": "",
        "author_email": "",
    },
    "readme_lang": "auto",
    "private": False,
    "commit_message": "docs: generate README via GitHub Auto",
    "verify_ssl": True,
    "ca_bundle": "",
    "proxy": "",
    "license": "MIT",
}

_CONFIG_PATH = Path(__file__).resolve().parent / "config.json"


def load_config(explicit: str | None = None) -> dict:
    global _CONFIG_PATH
    cfg = deepcopy(DEFAULT_CONFIG)
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    else:
        script_dir = Path(__file__).resolve().parent
        candidates.append(script_dir / "config.json")
        candidates.append(Path.cwd() / "config.json")

    for cand in candidates:
        if cand.exists():
            _CONFIG_PATH = cand
            try:
                data = json.loads(cand.read_text(encoding="utf-8-sig"))
                if isinstance(data.get("llm"), dict):
                    cfg["llm"].update(data["llm"])
                if isinstance(data.get("github"), dict):
                    cfg["github"].update(data["github"])
                for key in ("readme_lang", "private", "commit_message"):
                    if key in data:
                        cfg[key] = data[key]
                if isinstance(data.get("verify_ssl"), bool):
                    cfg["verify_ssl"] = data["verify_ssl"]
                if isinstance(data.get("ca_bundle"), str):
                    cfg["ca_bundle"] = data["ca_bundle"]
                if isinstance(data.get("proxy"), str):
                    cfg["proxy"] = data["proxy"]
                if isinstance(data.get("license"), str):
                    cfg["license"] = data["license"]
                log(f"已加载配置文件: {cand}")
            except Exception as exc:
                raise ToolError(f"配置文件 {cand} 解析失败: {exc}") from exc
            break
    else:
        _CONFIG_PATH = Path(explicit) if explicit else Path(__file__).resolve().parent / "config.json"

    # 环境变量优先
    cfg["llm"]["api_key"] = (
        os.environ.get("OPENAI_API_KEY")
        or os.environ.get("LLM_API_KEY")
        or cfg["llm"].get("api_key", "")
    )
    cfg["llm"]["base_url"] = os.environ.get("LLM_BASE_URL") or cfg["llm"]["base_url"]
    cfg["llm"]["model"] = os.environ.get("LLM_MODEL") or cfg["llm"]["model"]
    cfg["github"]["token"] = (
        os.environ.get("GITHUB_TOKEN")
        or os.environ.get("GH_TOKEN")
        or cfg["github"].get("token", "")
    )
    env_verify = (
        os.environ.get("SSL_VERIFY")
        or os.environ.get("GITHUB_SSL_VERIFY")
        or os.environ.get("LLM_SSL_VERIFY")
    )
    if env_verify is not None:
        cfg["verify_ssl"] = str(env_verify).lower() not in ("0", "false", "no", "off")
    if os.environ.get("CA_BUNDLE"):
        cfg["ca_bundle"] = os.environ["CA_BUNDLE"]
    if os.environ.get("PROXY") is not None:
        cfg["proxy"] = os.environ["PROXY"]
    return cfg


def _mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "****"
    return f"{value[:4]}****{value[-4:]}"


def save_config(cfg: dict, path: str | None = None) -> None:
    """把运行时配置写回 config.json（保留文件中已有的未知字段）。"""
    target = Path(path) if path else _CONFIG_PATH
    data: dict = {}
    if target.exists():
        try:
            data = json.loads(target.read_text(encoding="utf-8-sig"))
        except Exception:
            data = {}
    data.setdefault("llm", {})
    data.setdefault("github", {})
    data["llm"]["api_key"] = cfg["llm"].get("api_key", "")
    data["llm"]["base_url"] = cfg["llm"].get("base_url", "")
    data["llm"]["model"] = cfg["llm"].get("model", "")
    data["github"]["token"] = cfg["github"].get("token", "")
    data["github"]["author_name"] = cfg["github"].get("author_name", "")
    data["github"]["author_email"] = cfg["github"].get("author_email", "")
    data["readme_lang"] = cfg.get("readme_lang", "auto")
    data["private"] = bool(cfg.get("private", False))
    data["commit_message"] = cfg.get("commit_message", DEFAULT_CONFIG["commit_message"])
    data["verify_ssl"] = bool(cfg.get("verify_ssl", True))
    data["ca_bundle"] = cfg.get("ca_bundle", "")
    data["proxy"] = cfg.get("proxy", "")
    data["license"] = cfg.get("license", "MIT")
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    log(f"配置已保存到 {target}", "OK")


# --------------------------------------------------------------------------
# SSL 上下文（解决代理/公司网络下证书校验失败）
# --------------------------------------------------------------------------

@lru_cache(maxsize=8)
def _build_ssl_context(verify: bool, ca_bundle: str, warn: bool = True) -> ssl.SSLContext:
    if not verify:
        if warn:
            log("SSL 证书校验已关闭（verify_ssl=false），仅推荐用于代理/内网环境", "WARN")
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    try:
        ctx = ssl.create_default_context(cafile=ca_bundle or None)
    except (OSError, ssl.SSLError) as exc:
        raise ToolError(f"无法加载 CA 证书文件（{ca_bundle}）: {exc}") from exc
    # Windows：把系统证书库里的根证书也加进来，
    # 解决浏览器能访问、Python 却报 CERTIFICATE_VERIFY_FAILED 的问题（代理/安全软件 MITM）。
    if os.name == "nt":
        try:
            pem_parts = []
            for store in ("ROOT", "CA"):
                for cert, encoding, _trust in ssl.enum_certificates(store):
                    try:
                        if encoding == ssl.ENCODING_DER:
                            pem_parts.append(ssl.DER_cert_to_PEM_cert(cert))
                        elif encoding == ssl.ENCODING_X509_ASN:
                            pem_parts.append(ssl.X509_ASN1_cert_to_PEM_cert(cert))
                    except Exception:
                        continue
            if pem_parts:
                ctx.load_verify_locations(cadata="\n".join(pem_parts))
        except Exception:
            pass
    return ctx


_SSL_CONTEXT = None
_OPENER = None
_DIRECT_OPENER = None
_PROXY_STR = ""
_PROXY_BROKEN = False


def describe_proxies() -> str:
    """返回当前生效的代理设置（脱敏），便于诊断。"""
    try:
        proxies = request.getproxies()
    except Exception:
        return ""
    cleaned = []
    for scheme, url in sorted(proxies.items()):
        url = re.sub(r"://[^@/]*@", "://***@", url)
        cleaned.append(f"{scheme}={url}")
    return ", ".join(cleaned)


def _build_opener(proxy: str, ctx: ssl.SSLContext) -> request.OpenerDirector:
    handlers = [request.HTTPSHandler(context=ctx), request.HTTPHandler()]
    if proxy == "none":
        handlers.append(request.ProxyHandler({}))  # 直连，禁用代理
    elif proxy:
        handlers.append(request.ProxyHandler({"http": proxy, "https": proxy}))
    else:
        handlers.append(request.ProxyHandler())  # 跟随系统代理
    return request.build_opener(*handlers)


def configure_network(cfg: dict) -> None:
    global _SSL_CONTEXT, _OPENER, _DIRECT_OPENER, _PROXY_STR, _PROXY_BROKEN
    _SSL_CONTEXT = _build_ssl_context(
        bool(cfg.get("verify_ssl", True)),
        cfg.get("ca_bundle") or "",
    )
    proxy = str(cfg.get("proxy", "") or "").strip()
    _PROXY_STR = proxy
    _PROXY_BROKEN = False
    _OPENER = _build_opener(proxy, _SSL_CONTEXT)
    _DIRECT_OPENER = _build_opener("none", _SSL_CONTEXT)
    if proxy == "none":
        log("代理已禁用（proxy=none），将直连 GitHub/AI 接口", "WARN")
    elif proxy:
        log(f"使用代理: {proxy}")
    else:
        proxies = describe_proxies()
        if proxies:
            log(f"检测到系统代理: {proxies}")
            if any(v.strip().lower().startswith("socks") for v in request.getproxies().values()):
                log(
                    "检测到 SOCKS 代理，Python 标准库不支持 SOCKS。"
                    "请在 config.json 中设置 proxy 为 HTTP 代理地址（如 http://127.0.0.1:7890）或 \"none\"",
                    "WARN",
                )


def configure_ssl(cfg: dict) -> None:
    """兼容入口：同时配置 SSL 与代理。"""
    configure_network(cfg)


def get_ssl_context() -> ssl.SSLContext:
    return _SSL_CONTEXT if _SSL_CONTEXT is not None else _build_ssl_context(True, "")


def _fresh_request(req) -> request.Request:
    # 走代理失败后 req.host 会被改成代理地址，必须重建请求
    return request.Request(
        req.full_url,
        data=req.data,
        headers=dict(req.headers),
        method=req.get_method(),
    )


def open_url(req, timeout: int):
    """带 SSL + 代理配置的请求；失败时按「代理 → 直连 → 关闭校验走代理」自动降级。"""
    global _PROXY_BROKEN
    ctx = get_ssl_context()
    default_opener = _OPENER if _OPENER is not None else request.build_opener(
        request.HTTPSHandler(context=ctx), request.ProxyHandler()
    )
    using_proxy = _OPENER is not None and _OPENER is not _DIRECT_OPENER
    direct_opener = _DIRECT_OPENER if _DIRECT_OPENER is not None else request.build_opener(
        request.HTTPSHandler(context=ctx), request.ProxyHandler({})
    )

    candidates: list[tuple[request.OpenerDirector, str]] = []
    if using_proxy and not _PROXY_BROKEN:
        candidates.append((default_opener, "代理"))
    candidates.append((direct_opener, "直连"))
    if using_proxy and ctx.verify_mode != ssl.CERT_NONE:
        insecure_ctx = _build_ssl_context(False, "", warn=False)
        candidates.append(
            (_build_opener(_PROXY_STR, insecure_ctx), "关闭校验走代理")
        )

    last_error: error.URLError | None = None
    for opener, label in candidates:
        try:
            return opener.open(_fresh_request(req), timeout=timeout)
        except error.URLError as exc:
            last_error = exc
            if isinstance(exc.reason, (ConnectionRefusedError, ssl.SSLError)):
                if label == "代理":
                    _PROXY_BROKEN = True
                log(f"{label}请求失败（{type(exc.reason).__name__}），尝试下一种方式…", "WARN")
                continue
            raise
    raise last_error


# --------------------------------------------------------------------------
# git 辅助
# --------------------------------------------------------------------------

def run_git(root: Path, args: list[str], check: bool = True, env: dict | None = None):
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=300,
        )
    except FileNotFoundError as exc:
        raise ToolError("未找到 git，请先安装并确保 git 在 PATH 中") from exc
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise ToolError(f"git {' '.join(args)} 失败: {detail}")
    return result


def _is_valid_git_repo(root: Path) -> bool:
    r = run_git(root, ["rev-parse", "--is-inside-work-tree"], check=False)
    return r.returncode == 0 and r.stdout.strip() == "true"


def ensure_git_repo(root: Path, dry_run: bool) -> None:
    if _is_valid_git_repo(root):
        if not dry_run:
            run_git(root, ["branch", "-M", "main"], check=False)
        return
    log("项目不是有效 git 仓库，将执行 git init -b main", "WARN")
    if dry_run:
        return
    try:
        run_git(root, ["init", "-b", "main"])
    except ToolError as exc:
        # .git 可能是损坏的文件/目录（如指向不存在路径的 gitfile），
        # 先把它备份挪开，再重新初始化（可恢复，不会删除数据）
        broken = root / ".git"
        backup = root / f".git.bak-{time.time_ns()}"
        if broken.exists():
            log(f".git 损坏（{exc}），先移动到 {backup} 再重新初始化", "WARN")
            try:
                broken.rename(backup)
            except OSError as move_exc:
                raise ToolError(
                    f"git init 失败且无法移动损坏的 .git（{move_exc}），请手动处理 {broken}"
                ) from exc
            run_git(root, ["init", "-b", "main"])
        else:
            raise
    if not _is_valid_git_repo(root):
        raise ToolError(f"git init 后仓库仍无效: {root}")


def ensure_identity(root: Path, cfg: dict, dry_run: bool) -> None:
    name = cfg["github"].get("author_name", "").strip()
    email = cfg["github"].get("author_email", "").strip()

    if not name:
        r = run_git(root, ["config", "user.name"], check=False)
        name = r.stdout.strip() if r.returncode == 0 else ""
    if not email:
        r = run_git(root, ["config", "user.email"], check=False)
        email = r.stdout.strip() if r.returncode == 0 else ""
    if not name:
        name = "GitHub Auto"
    if not email:
        email = "github-auto@users.noreply.github.com"

    log(f"git 身份: {name} <{email}>")
    if dry_run:
        return
    run_git(root, ["config", "user.name", name])
    run_git(root, ["config", "user.email", email])


def commit_changes(root: Path, message: str, dry_run: bool) -> bool:
    status = run_git(root, ["status", "--porcelain"], check=False)
    if not status.stdout.strip():
        log("没有待提交的变更，跳过 commit")
        return False
    log(f"将执行: git add -A && git commit -m \"{message}\"")
    if dry_run:
        return True
    run_git(root, ["add", "-A"])
    run_git(root, ["commit", "-m", message])
    log("已提交变更", "OK")
    return True


def _git_auth_pairs(
    token: str,
    verify_ssl: bool,
    proxy: str,
    force_direct: bool,
    use_token: bool,
) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    if not verify_ssl:
        log("git 推送将跳过 SSL 校验（verify_ssl=false）", "WARN")
        pairs.append(("http.sslVerify", "false"))
    effective = "none" if force_direct else proxy
    if effective == "none":
        pairs.append(("http.proxy", ""))
        pairs.append(("https.proxy", ""))
    elif effective:
        log(f"git 推送将使用代理: {effective}")
        pairs.append(("http.proxy", effective))
        pairs.append(("https.proxy", effective))
    if use_token:
        # GitHub 的 git 服务不接受 Bearer 头（经典 PAT 会 401），必须用 Basic
        auth = "Basic " + base64.b64encode(f"x-access-token:{token}".encode()).decode()
        pairs.insert(0, ("http.extraheader", f"AUTHORIZATION: {auth}"))
        pairs.append(("credential.helper", ""))
    return pairs


def _git_env(pairs: list[tuple[str, str]]) -> dict:
    env = dict(os.environ)
    env["GIT_CONFIG_COUNT"] = str(len(pairs))
    for idx, (key, value) in enumerate(pairs):
        env[f"GIT_CONFIG_KEY_{idx}"] = key
        env[f"GIT_CONFIG_VALUE_{idx}"] = value
    return env


def _git_proxy_info(root: Path) -> str:
    parts: list[str] = []
    for key in ("http.proxy", "https.proxy"):
        r = run_git(root, ["config", "--get", key], check=False)
        if r.returncode == 0 and r.stdout.strip():
            parts.append(f"{key}={r.stdout.strip()}")
    for env_key in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY"):
        if os.environ.get(env_key):
            parts.append(f"{env_key}={os.environ[env_key]}")
    return ", ".join(parts)


_CONN_ERROR_HINTS = (
    "unable to access",
    "could not connect",
    "failed to connect",
    "connection refused",
    "connection timed out",
    "timed out",
    "early eof",
    "proxy",
)


def _looks_like_conn_error(msg: str) -> bool:
    low = str(msg).lower()
    return any(hint in low for hint in _CONN_ERROR_HINTS)


_AUTH_ERROR_HINTS = (
    "invalid credentials",
    "authentication failed",
    "could not read username",
    "could not read password",
    "could not read prompt",
    "terminal prompts disabled",
    "401",
    "403",
    "permission denied",
    "askpass",
)

_REJECT_HINTS = (
    "non-fast-forward",
    "failed to push some refs",
    "fetch first",
    "cannot update the ref",
    "stale info",
    "rejected",
)


def _classify_git_error(msg: str) -> str:
    low = str(msg).lower()
    if any(hint in low for hint in _REJECT_HINTS):
        return "rejected"
    if _looks_like_conn_error(low):
        return "conn"
    if any(hint in low for hint in _AUTH_ERROR_HINTS):
        return "auth"
    return "other"


def push_to_remote(
    root: Path,
    token: str,
    dry_run: bool,
    verify_ssl: bool = True,
    proxy: str = "",
) -> None:
    log("将执行: git push -u origin main")
    if dry_run:
        return
    proxy_info = _git_proxy_info(root)
    if proxy_info and not proxy:
        log(f"检测到 git 代理配置: {proxy_info}（连接失败时会自动尝试直连）")

    # 尝试矩阵：(是否用工具 token, 是否强制直连)
    attempts: list[tuple[bool, bool]] = []
    if token:
        attempts.append((True, False))
    attempts.append((False, False))
    if proxy != "none":
        if token:
            attempts.append((True, True))
        attempts.append((False, True))

    last_error: ToolError | None = None
    branch_pushed = False
    for idx, (use_token, force_direct) in enumerate(attempts):
        pairs = _git_auth_pairs(token, verify_ssl, proxy, force_direct, use_token)
        env = _git_env(pairs)
        # 禁止交互式提示，避免 GIT_ASKPASS 等外部脚本卡住/报错
        env["GIT_TERMINAL_PROMPT"] = "0"
        env.pop("GIT_ASKPASS", None)
        env.pop("SSH_ASKPASS", None)
        try:
            run_git(root, ["push", "-u", "origin", "main"], env=env)
            log("推送成功 🎉", "OK")
            return
        except ToolError as exc:
            last_error = exc
            kind = _classify_git_error(str(exc))
            if kind == "rejected" and not branch_pushed:
                branch = f"auto-{time.strftime('%Y%m%d-%H%M%S')}"
                log(
                    f"main 分支被拒绝（远端已有不同历史），改为推送到新分支 {branch}…",
                    "WARN",
                )
                try:
                    run_git(root, ["push", "-u", "origin", f"HEAD:{branch}"], env=env)
                    log(f"已推送到新分支 {branch} 🎉", "OK")
                    return
                except ToolError as branch_exc:
                    last_error = branch_exc
                    branch_pushed = True
                    break
            if idx + 1 < len(attempts):
                log(
                    f"推送尝试 {idx + 1}/{len(attempts)} 失败（{kind}），换用下一种方式…",
                    "WARN",
                )
            if kind == "other" or idx + 1 >= len(attempts):
                break

    hint = ""
    kind = _classify_git_error(str(last_error) if last_error else "")
    if kind == "conn":
        hint = (
            f"。检测到代理配置: {proxy_info or '无'}。"
            "可运行 git config --global --unset http.proxy 和 "
            "git config --global --unset https.proxy 移除 git 自身代理，"
            "或在 config.json 设置 \"proxy\": \"none\" 强制直连"
        )
    elif kind == "auth":
        hint = (
            "。已尝试 GitHub token（Basic 认证）和 git 已有凭据。"
            "请确认 config.json / GITHUB_TOKEN 中的 token 有效且有 repo 权限，"
            "或运行 git credential-manager github login 登录后重试"
        )
    raise ToolError(f"{last_error}{hint}") from last_error


# --------------------------------------------------------------------------
# GitHub REST API（不依赖 gh CLI）
# --------------------------------------------------------------------------

def gh_api(url: str, token: str, method: str = "GET", payload: dict | None = None):
    for _ in range(4):
        body = None
        headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "GitHub-Auto",
        }
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = request.Request(url, data=body, headers=headers, method=method)
        try:
            with open_url(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except error.HTTPError as exc:
            if exc.code == 307:
                location = (exc.headers.get("Location") or "").strip()
                if location and urlparse(location).netloc == "api.github.com":
                    log(f"GitHub API 返回 307，自动跟随重定向: {location}", "WARN")
                    url = location
                    continue
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            if exc.code == 401:
                raise ToolError("GitHub token 无效或已过期，请检查 GITHUB_TOKEN") from exc
            if exc.code == 422 and payload and "name" in payload:
                return {"conflict": True}
            raise ToolError(f"GitHub API 请求失败 ({exc.code}): {detail[:300]}") from exc
        except error.URLError as exc:
            if isinstance(exc.reason, ssl.SSLError):
                raise ToolError(
                    f"GitHub API SSL 证书校验失败: {exc.reason}。"
                    "如使用代理/公司网络，可在 config.json 中设置 \"verify_ssl\": false，"
                    "或用 ca_bundle 指定公司 CA 证书路径"
                ) from exc
            proxies = describe_proxies()
            hint = (
                f"检测到代理（{proxies}），请确认代理软件已启动；"
                "或在 config.json 中设置 \"proxy\": \"none\" 直连，"
                "或 \"proxy\": \"http://127.0.0.1:7890\" 指定可用代理"
                if proxies
                else "请检查网络/防火墙，确认 api.github.com 可以访问"
            )
            raise ToolError(f"无法连接 GitHub API: {exc.reason}。{hint}") from exc


def gh_get_login(token: str) -> str:
    data = gh_api("https://api.github.com/user", token)
    login = data.get("login")
    if not login:
        raise ToolError("无法获取 GitHub 用户名，请检查 token 权限")
    return login


def sanitize_repo_name(name: str) -> str:
    name = name.strip().lower().replace(" ", "-")
    name = re.sub(r"[^a-z0-9._-]", "-", name)
    name = re.sub(r"-{2,}", "-", name).strip("-.")
    if not name:
        name = "my-project"
    return name[:100]


def gh_create_repo(owner: str, name: str, description: str, private: bool, token: str) -> dict:
    payload = {
        "name": name,
        "description": description[:120],
        "private": bool(private),
        "auto_init": False,
    }
    data = gh_api("https://api.github.com/user/repos", token, method="POST", payload=payload)
    # 同名仓库已存在时返回 {"conflict": True}，由调用方决定直接使用现有仓库
    if data.get("conflict"):
        return {"conflict": True, "name": name}
    return data


def parse_github_url(url: str) -> tuple[str, str] | None:
    """从远程地址解析 (owner, repo)，支持 https 与 ssh 两种格式。"""
    m = re.match(
        r"(?:https?://|ssh://|git@)(?:www\.)?github\.com[:/]([^/]+)/([^/.]+)",
        url,
    )
    if m:
        return m.group(1), m.group(2)
    return None


def repo_description(root: Path, analysis: dict) -> str:
    """生成仓库 About 描述：优先取 README 的 slogan，其次元数据描述。"""
    readme = root / "README.md"
    if readme.exists():
        lines = _read_text(readme).splitlines()
        for idx, line in enumerate(lines):
            if line.startswith("# "):
                for nxt in lines[idx + 1 : idx + 5]:
                    s = nxt.strip()
                    if s.startswith(">"):
                        return s.lstrip(">").strip()[:300]
                break
    md = analysis.get("metadata") or {}
    if md.get("description"):
        return str(md["description"]).strip()[:300]
    langs = ", ".join(list(analysis.get("languages", {}))[:3]) or "多种技术"
    return f"{analysis['name']} - 使用 {langs} 开发的项目"[:300]


# --------------------------------------------------------------------------
# 项目分析
# --------------------------------------------------------------------------

def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig", errors="replace")
    except Exception:
        return ""


def _re_value(content: str, key: str) -> str | None:
    m = re.search(rf"^{key}\s*=\s*[\"']([^\"']+)[\"']", content, re.MULTILINE)
    return m.group(1).strip() if m else None


def parse_manifest(root: Path) -> dict | None:
    m: dict = {}

    pkg_json = root / "package.json"
    if pkg_json.exists():
        try:
            data = json.loads(_read_text(pkg_json))
            m["type"] = "Node.js / npm"
            m["name"] = data.get("name")
            m["version"] = data.get("version")
            m["description"] = data.get("description")
            scripts = data.get("scripts", {}) or {}
            m["scripts"] = [f"{k}: {v}" for k, v in list(scripts.items())[:10]]
            deps = {**(data.get("dependencies") or {}), **(data.get("devDependencies") or {})}
            m["dependencies"] = list(deps.keys())[:30]
        except Exception:
            pass
        return m or None

    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        content = _read_text(pyproject)
        m["type"] = "Python"
        m["name"] = _re_value(content, "name")
        m["version"] = _re_value(content, "version")
        m["description"] = _re_value(content, "description")
        deps_match = re.search(r"dependencies\s*=\s*\[(.*?)\]", content, re.DOTALL)
        if deps_match:
            m["dependencies"] = re.findall(r"[\"']([^\"']+)[\"']", deps_match.group(1))[:30]
        scripts_match = re.search(r"\[project\.scripts\](.*?)(?=\n\[|\Z)", content, re.DOTALL)
        if scripts_match:
            m["scripts"] = [line.strip() for line in scripts_match.group(1).splitlines() if line.strip()][:10]
        return m or None

    req = root / "requirements.txt"
    if req.exists():
        lines = [ln.strip() for ln in _read_text(req).splitlines() if ln.strip() and not ln.startswith("#")]
        m = {"type": "Python", "dependencies": lines[:30]}
        return m

    cargo = root / "Cargo.toml"
    if cargo.exists():
        content = _read_text(cargo)
        m["type"] = "Rust"
        m["name"] = _re_value(content, "name")
        m["version"] = _re_value(content, "version")
        m["description"] = _re_value(content, "description")
        return m or None

    go_mod = root / "go.mod"
    if go_mod.exists():
        content = _read_text(go_mod)
        mm = re.search(r"^module\s+(.+)$", content, re.MULTILINE)
        m = {"type": "Go", "name": mm.group(1).strip() if mm else None}
        return m

    setup_py = root / "setup.py"
    if setup_py.exists():
        content = _read_text(setup_py)
        m["type"] = "Python"
        m["name"] = _re_value(content, "name")
        m["version"] = _re_value(content, "version")
        m["description"] = _re_value(content, "description")
        return m or None

    composer = root / "composer.json"
    if composer.exists():
        try:
            data = json.loads(_read_text(composer))
            m = {"type": "PHP", "name": data.get("name"), "description": data.get("description")}
        except Exception:
            pass
        return m or None

    return None


def detect_license(root: Path) -> str | None:
    for f in root.iterdir():
        if not f.is_file():
            continue
        upper = f.name.upper()
        if upper in {"LICENSE", "LICENSE.MD", "LICENSE.TXT", "LICENSE.MIT", "COPYING"}:
            content = _read_text(f).lower()
            for key, label in LICENSE_NAMES.items():
                if key in content:
                    return label
            return "See LICENSE"
    return None


def git_info(root: Path) -> dict:
    if not (root / ".git").exists():
        return {}
    info: dict = {}
    r = run_git(root, ["log", "-10", "--pretty=format:%h|%ad|%s", "--date=short"], check=False)
    if r.returncode == 0 and r.stdout.strip():
        info["recent_commits"] = r.stdout.strip().splitlines()
    r = run_git(root, ["remote", "get-url", "origin"], check=False)
    if r.returncode == 0 and r.stdout.strip():
        info["remote"] = r.stdout.strip()
    r = run_git(root, ["branch", "--show-current"], check=False)
    if r.returncode == 0 and r.stdout.strip():
        info["branch"] = r.stdout.strip()
    return info


def build_tree(root: Path, max_depth: int = 2, max_entries: int = 60) -> str:
    lines = [f"{root.name}/"]

    def walk(rel: Path, depth: int) -> None:
        base = root if rel == Path(".") else root / rel
        try:
            entries = sorted(base.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
        except OSError:
            return
        shown = 0
        for entry in entries:
            if entry.name in SKIP_DIRS or entry.name.startswith("."):
                continue
            if shown >= max_entries:
                lines.append("    " * (depth + 1) + "└── ...")
                return
            shown += 1
            indent = "    " * (depth + 1)
            if entry.is_dir():
                lines.append(f"{indent}├── {entry.name}/")
                if depth < max_depth:
                    walk(entry.relative_to(root), depth + 1)
            else:
                lines.append(f"{indent}├── {entry.name}")

    walk(Path("."), 0)
    return "\n".join(lines)


def guess_run_commands(root: Path, metadata: dict | None) -> list[str]:
    cmds: list[str] = []
    mtype = (metadata or {}).get("type", "")
    if "Node.js" in mtype:
        cmds.append("npm install")
        scripts = [s.split(":")[0] for s in (metadata or {}).get("scripts", [])]
        if any(s in ("dev", "start") for s in scripts):
            cmds.append(f"npm run {scripts[0]}")
        else:
            cmds.append("npm start")
    elif mtype == "Python":
        if (root / "requirements.txt").exists():
            cmds.append("pip install -r requirements.txt")
        elif (root / "pyproject.toml").exists():
            cmds.append("pip install -e .")
        for fname in ("main.py", "app.py", "cli.py", "run.py"):
            if (root / fname).exists():
                cmds.append(f"python {fname}")
                break
    elif mtype == "Rust":
        cmds.append("cargo build --release")
        cmds.append("cargo run")
    elif mtype == "Go":
        cmds.append("go build")
        cmds.append("go run .")
    if not cmds and (root / "Makefile").exists():
        cmds.append("make")
    if not cmds and (root / "Dockerfile").exists():
        cmds.append("docker build -t <image-name> .")
    return cmds


def existing_readme(root: Path) -> str:
    for f in sorted(root.iterdir()):
        if f.is_file() and f.name.upper().startswith("README"):
            return _read_text(f)[:800]
    return ""


def collect_project_files(root: Path) -> dict:
    lang_files: Counter = Counter()
    lang_lines: Counter = Counter()
    total_lines = 0
    total_files = 0
    analyzed = 0

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in filenames:
            if analyzed >= 3000:
                break
            path = Path(dirpath) / fn
            suffix = path.suffix.lower()
            if suffix in BINARY_EXTS:
                continue
            total_files += 1
            lang = LANG_MAP.get(suffix)
            if lang:
                lang_files[lang] += 1
            is_text = lang or suffix in TEXT_EXTS or suffix == ""
            if not is_text:
                continue
            try:
                if path.stat().st_size > 1_000_000:
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
                count = text.count("\n") + 1
                total_lines += count
                if lang:
                    lang_lines[lang] += count
                analyzed += 1
            except OSError:
                continue

    languages = {}
    for lang in lang_files:
        languages[lang] = {"files": lang_files[lang], "lines": lang_lines.get(lang, 0)}
    languages = dict(sorted(languages.items(), key=lambda kv: -kv[1]["files"])[:8])
    return {
        "languages": languages,
        "total_files": total_files,
        "total_lines": total_lines,
        "top_level": sorted(
            e.name for e in root.iterdir() if e.name != ".git"
        )[:60],
        "tree": build_tree(root),
    }


def analyze_project(root: Path) -> dict:
    root = Path(root)
    if not root.exists() or not root.is_dir():
        raise ToolError(f"路径不存在或不是文件夹: {root}")

    files_info = collect_project_files(root)
    metadata = parse_manifest(root)
    license_name = detect_license(root)
    readme_excerpt = existing_readme(root)
    workflows = []
    wf_dir = root / ".github" / "workflows"
    if wf_dir.exists():
        workflows = sorted(f.name for f in wf_dir.glob("*") if f.is_file())

    analysis = {
        "name": root.name,
        "root": str(root.resolve()),
        **files_info,
        "metadata": metadata,
        "license": license_name,
        "run_hint": guess_run_commands(root, metadata),
        "has_dockerfile": (root / "Dockerfile").exists(),
        "has_makefile": (root / "Makefile").exists(),
        "workflows": workflows,
        "has_readme": bool(readme_excerpt),
        "existing_readme_excerpt": readme_excerpt,
        "git": git_info(root),
    }
    return analysis


# --------------------------------------------------------------------------
# 依赖文件生成（自动补全项目）
# --------------------------------------------------------------------------

PY_STDLIB = set(getattr(sys, "stdlib_module_names", ())) | {
    "__future__", "typing", "dataclasses", "enum", "abc", "collections",
    "functools", "itertools", "operator", "pathlib", "re", "os", "sys",
    "json", "csv", "sqlite3", "datetime", "time", "math", "random",
    "string", "subprocess", "threading", "multiprocessing", "logging",
    "argparse", "asyncio", "io", "tempfile", "shutil", "glob", "hashlib",
    "hmac", "base64", "uuid", "copy", "warnings", "traceback", "weakref",
    "contextlib", "types", "unicodedata", "bisect", "heapq", "queue",
    "socket", "email", "http", "urllib", "xml", "html", "webbrowser",
    "configparser", "statistics", "decimal", "fractions", "getpass",
    "platform", "signal", "struct", "zipfile", "tarfile", "gzip",
    "zlib", "pickle", "shelve", "dbm", "select", "selectors", "ssl",
    "tkinter", "curses", "turtle", "unittest", "doctest", "pdb",
    "venv", "zoneinfo", "importlib", "pkgutil", "runpy", "site",
    "ctypes", "cProfile", "profile", "timeit", "wave", "colorsys",
    "gettext", "locale", "codecs", "dis", "inspect", "linecache",
    "marshal", "optparse", "token", "tokenize", "keyword", "ast",
    "compileall", "difflib", "fnmatch", "ftplib", "imaplib", "nntplib",
    "poplib", "smtplib", "telnetlib", "xmlrpc", "wsgiref", "cgi",
    "cgitb", "mailbox", "mimetypes", "quopri", "uu", "xdrlib",
}

PY_PKG_MAP = {
    "yaml": "PyYAML", "PIL": "Pillow", "cv2": "opencv-python",
    "sklearn": "scikit-learn", "bs4": "beautifulsoup4",
    "dotenv": "python-dotenv", "dateutil": "python-dateutil",
    "cryptography": "cryptography", "matplotlib": "matplotlib",
    "numpy": "numpy", "pandas": "pandas", "flask": "Flask",
    "django": "Django", "requests": "requests", "fastapi": "fastapi",
    "pydantic": "pydantic", "tqdm": "tqdm", "click": "click",
    "typer": "typer", "aiohttp": "aiohttp", "sqlalchemy": "SQLAlchemy",
    "redis": "redis", "pymongo": "pymongo", "psycopg2": "psycopg2-binary",
    "selenium": "selenium", "httpx": "httpx", "jinja2": "Jinja2",
    "gunicorn": "gunicorn", "uvicorn": "uvicorn", "torch": "torch",
    "tensorflow": "tensorflow", "keras": "keras", "scipy": "scipy",
    "pytest": "pytest", "celery": "celery", "sentry_sdk": "sentry-sdk",
    "bson": "pymongo", "PIL.Image": "Pillow",
}

NODE_BUILTINS = {
    "fs", "path", "http", "https", "os", "crypto", "stream", "util",
    "events", "child_process", "url", "querystring", "zlib", "buffer",
    "net", "tls", "dgram", "cluster", "dns", "readline", "repl", "timers",
    "tty", "v8", "vm", "wasi", "worker_threads", "assert", "async_hooks",
    "constants", "domain", "module", "perf_hooks", "process", "punycode",
    "string_decoder", "sys", "trace_events", "inspector", "node:test",
}


def _iter_source_files(root: Path, exts: set[str]):
    for f in root.rglob("*"):
        if not f.is_file() or f.suffix.lower() not in exts:
            continue
        rel = f.relative_to(root)
        if any(part in SKIP_DIRS or part.startswith(".") for part in rel.parts):
            continue
        yield f


def _local_names(root: Path, exts: set[str]) -> set[str]:
    names: set[str] = set()
    for e in root.iterdir():
        if e.is_file() and e.suffix.lower() in exts:
            names.add(e.stem)
        elif e.is_dir() and not e.name.startswith(".") and e.name not in SKIP_DIRS:
            names.add(e.name)
    # 项目内任意位置的目录/模块都视为本地，避免嵌套子包被误判为第三方依赖
    for f in root.rglob("*"):
        rel = f.relative_to(root)
        if any(part in SKIP_DIRS or part.startswith(".") for part in rel.parts):
            continue
        if f.is_dir():
            names.add(f.name)
        elif f.suffix.lower() in exts:
            names.add(f.stem)
    return names


def detect_python_deps(root: Path) -> list[str]:
    local = _local_names(root, {".py"})
    imports: set[str] = set()
    for f in _iter_source_files(root, {".py"}):
        content = _read_text(f)
        imports.update(
            re.findall(r"^\s*(?:import|from)\s+([a-zA-Z_][a-zA-Z0-9_]*)", content, re.M)
        )
    pkgs: set[str] = set()
    for name in imports:
        if name in local or name in PY_STDLIB:
            continue
        pkgs.add(PY_PKG_MAP.get(name, name))
    return sorted(pkgs)


def detect_node_deps(root: Path) -> list[str]:
    exts = {".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs"}
    local = _local_names(root, exts)
    mods: set[str] = set()
    for f in _iter_source_files(root, exts):
        content = _read_text(f)
        mods.update(re.findall(r"require\(['\"]([^'\"]+)['\"]\)", content))
        mods.update(re.findall(r"from\s+['\"]([^'\"]+)['\"]", content))
        mods.update(re.findall(r"^\s*import\s+['\"]([^'\"]+)['\"]", content, re.M))
    deps: set[str] = set()
    for m in mods:
        if m.startswith("./") or m.startswith("../") or m.startswith("."):
            continue
        if m.startswith("@"):
            parts = m.split("/")
            name = "/".join(parts[:2]) if len(parts) >= 2 else parts[0]
        else:
            name = m.split("/")[0]
        if name in NODE_BUILTINS or name in local:
            continue
        deps.add(name)
    return sorted(deps)


def _is_external_go(path: str) -> bool:
    return "." in path.split("/")[0]


def detect_go_deps(root: Path) -> list[str]:
    deps: set[str] = set()
    for f in _iter_source_files(root, {".go"}):
        content = _read_text(f)
        in_block = False
        for line in content.splitlines():
            s = line.strip()
            if s.startswith("import"):
                rest = s[6:].strip()
                if rest.startswith("("):
                    in_block = True
                elif rest.startswith('"'):
                    path = rest.strip('"')
                    if _is_external_go(path):
                        deps.add(path)
            elif in_block:
                if s.startswith(")"):
                    in_block = False
                elif s.startswith('"'):
                    path = s.strip('"')
                    if _is_external_go(path):
                        deps.add(path)
    return sorted(deps)


def detect_rust_deps(root: Path) -> list[str]:
    local = _local_names(root, {".rs"})
    rust_std = {"std", "core", "alloc", "proc_macro", "test", "self", "super", "crate"}
    crates: set[str] = set()
    for f in _iter_source_files(root, {".rs"}):
        content = _read_text(f)
        for m in re.finditer(r"^\s*use\s+([a-zA-Z0-9_]+)", content, re.M):
            crate = m.group(1)
            if crate not in rust_std and crate not in local:
                crates.add(crate)
    return sorted(crates)


def _detect_node_entry(root: Path) -> str | None:
    for name in (
        "index.js", "index.ts", "main.js", "app.js", "server.js",
        "src/index.js", "src/index.ts", "src/main.js", "src/app.js",
        "src/server.js",
    ):
        if (root / name).exists():
            return name
    return None


def build_gitignore(langs: list[str]) -> str:
    sections: list[str] = []
    if "Python" in langs:
        sections.append("# Python\n__pycache__/\n*.py[cod]\n*.egg-info/\n.venv/\nvenv/\nenv/\n.env\n.pytest_cache/\n.mypy_cache/\n.ruff_cache/")
    if any(l in langs for l in ("JavaScript", "TypeScript", "Vue", "Svelte")):
        sections.append("# Node.js\nnode_modules/\ndist/\nbuild/\n.next/\n*.log\nnpm-debug.log*\n.env")
    if "Go" in langs:
        sections.append("# Go\nbin/\n*.exe\n*.test\ncoverage.out")
    if "Rust" in langs:
        sections.append("# Rust\ntarget/")
    sections.append("# 通用\n.DS_Store\nThumbs.db\n.idea/\n.vscode/\n*.swp")
    return "\n\n".join(sections) + "\n"


MIT_LICENSE = """MIT License

Copyright (c) {year} {author}

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""


def generate_dependency_files(
    root: Path,
    analysis: dict | None = None,
    force: bool = False,
    dry_run: bool = False,
    cfg: dict | None = None,
) -> list[str]:
    """自动补全缺失的依赖/配置文件，返回创建的文件名列表。"""
    root = Path(root)
    if analysis is None:
        analysis = analyze_project(root)
    cfg = cfg or {}
    langs = list(analysis.get("languages", {}).keys())
    created: list[str] = []

    def _write(name: str, content: str) -> None:
        if dry_run:
            log(f"将创建 {name}")
            created.append(name)
            return
        (root / name).write_text(content, encoding="utf-8")
        created.append(name)
        log(f"已创建 {name}", "OK")

    # Python
    if "Python" in langs:
        has_manifest = any(
            (root / f).exists()
            for f in ("requirements.txt", "pyproject.toml", "setup.py", "Pipfile")
        )
        if has_manifest and not force:
            log("已存在 Python 依赖清单，跳过 requirements.txt")
        else:
            deps = detect_python_deps(root)
            if deps:
                _write("requirements.txt", "\n".join(deps) + "\n")
            else:
                log("未检测到第三方 Python 依赖，跳过 requirements.txt")

    # Node.js
    if any(l in langs for l in ("JavaScript", "TypeScript", "Vue", "Svelte")):
        pkg_json = root / "package.json"
        if pkg_json.exists() and not force:
            log("已存在 package.json，跳过")
        else:
            deps = detect_node_deps(root)
            entry = _detect_node_entry(root)
            scripts = {}
            if entry:
                scripts = {"start": f"node {entry}", "dev": f"node {entry}"}
            md = analysis.get("metadata") or {}
            pkg = {
                "name": sanitize_repo_name(root.name),
                "version": "0.1.0",
                "description": md.get("description") or f"{root.name} 项目",
                "main": entry or "index.js",
                "scripts": scripts,
                "dependencies": {d: "*" for d in deps},
            }
            _write(
                "package.json",
                json.dumps(pkg, ensure_ascii=False, indent=2) + "\n",
            )

    # Go
    if "Go" in langs:
        go_mod = root / "go.mod"
        if go_mod.exists() and not force:
            log("已存在 go.mod，跳过")
        else:
            deps = detect_go_deps(root)
            content = f"module {sanitize_repo_name(root.name)}\n\ngo 1.21\n"
            if deps:
                content += "\n// 运行 go mod tidy 拉取依赖\n"
            _write("go.mod", content)
            if not dry_run and deps and shutil.which("go"):
                log("检测到 go 工具链，运行 go mod tidy 补全依赖版本…")
                result = subprocess.run(
                    ["go", "mod", "tidy"],
                    cwd=str(root),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=180,
                )
                if result.returncode == 0:
                    log("go mod tidy 完成", "OK")
                else:
                    log(f"go mod tidy 失败（{result.stderr.strip()[:200]}），go.mod 需手动补全", "WARN")

    # Rust
    if "Rust" in langs:
        cargo = root / "Cargo.toml"
        if cargo.exists() and not force:
            log("已存在 Cargo.toml，跳过")
        else:
            deps = detect_rust_deps(root)
            lines = [
                "[package]",
                f'name = "{sanitize_repo_name(root.name)}"',
                'version = "0.1.0"',
                'edition = "2021"',
                "",
                "[dependencies]",
            ]
            lines += [f'{d} = "*"' for d in deps]
            _write("Cargo.toml", "\n".join(lines) + "\n")

    # .gitignore
    gitignore = root / ".gitignore"
    if gitignore.exists() and not force:
        log("已存在 .gitignore，跳过")
    else:
        _write(".gitignore", build_gitignore(langs))

    # LICENSE
    if not detect_license(root):
        license_name = str(cfg.get("license", "MIT") or "MIT").strip()
        author = cfg.get("github", {}).get("author_name") or "GitHub Auto"
        if license_name.upper() == "MIT":
            content = MIT_LICENSE.format(year=time.strftime("%Y"), author=author)
        else:
            content = (
                f"{license_name} License\n\n"
                f"Copyright (c) {time.strftime('%Y')} {author}\n"
            )
        _write("LICENSE", content)
    else:
        log("已存在 LICENSE，跳过")

    return created


# --------------------------------------------------------------------------
# README 生成
# --------------------------------------------------------------------------

def resolve_lang(analysis: dict, cfg_lang: str) -> str:
    if cfg_lang in ("zh", "en"):
        return cfg_lang
    excerpt = analysis.get("existing_readme_excerpt", "") or ""
    zh = len(re.findall(r"[\u4e00-\u9fff]", excerpt))
    # 已有 README 中含有明显中文内容时判定为中文（英文 README 通常不含中文字符）
    if zh >= 10:
        return "zh"
    if excerpt:
        return "en"
    return "zh"


def build_badges(analysis: dict) -> str:
    badges = []
    for lang in list(analysis.get("languages", {}).keys())[:3]:
        badges.append(f"![{lang}](https://img.shields.io/badge/{quote(lang)}-blue)")
    if analysis.get("license"):
        badges.append(
            f"![License](https://img.shields.io/badge/License-{quote(analysis['license'])}-green)"
        )
    return " ".join(badges) + "\n" if badges else ""


def fallback_readme(analysis: dict, lang: str) -> str:
    name = analysis["name"]
    langs = list(analysis.get("languages", {}).keys())
    metadata = analysis.get("metadata") or {}
    description = (metadata.get("description") or "").strip()
    lines = analysis.get("total_lines", 0)
    files = analysis.get("total_files", 0)
    license_name = analysis.get("license") or "MIT"
    run_hint = analysis.get("run_hint") or []

    if lang == "en":
        t = {
            "intro": "Introduction",
            "features": "Features",
            "start": "Getting Started",
            "install": "Installation",
            "run": "Run",
            "structure": "Project Structure",
            "screenshot": "Screenshots",
            "screenshot_note": "> Add screenshots or demo GIFs here.",
            "contribute": "Contributing",
            "contribute_note": "Issues and pull requests are welcome.",
            "license": "License",
            "tagline": (
                description
                or f"A project built with {', '.join(langs) if langs else 'modern technology'}."
            ),
        }
    else:
        t = {
            "intro": "项目简介",
            "features": "功能特性",
            "start": "快速开始",
            "install": "安装",
            "run": "运行",
            "structure": "项目结构",
            "screenshot": "截图",
            "screenshot_note": "> 在此处添加项目截图或演示动图。",
            "contribute": "参与贡献",
            "contribute_note": "欢迎提交 Issue 和 Pull Request。",
            "license": "许可证",
            "tagline": (
                description
                or f"一个使用 {', '.join(langs) if langs else '现代技术'} 开发的项目。"
            ),
        }

    stat_line = (
        f"{files} 个文件，{lines:,} 行代码" if lang == "zh"
        else f"{files} files, {lines:,} lines of code"
    )
    body = [
        f"# {name}",
        "",
        f"> {t['tagline']}",
        "",
        build_badges(analysis),
        f"## ✨ {t['intro']}",
        "",
        f"该项目使用 **{', '.join(langs) if langs else '多种技术'}** 编写，包含 {stat_line}。"
        if lang == "zh"
        else f"Built with **{', '.join(langs) if langs else 'modern technologies'}** ({stat_line}).",
        "",
        f"## 🚀 {t['start']}",
        "",
    ]
    if run_hint:
        body += [f"### {t['install']}", "", "```bash", run_hint[0], "```", ""]
        if len(run_hint) > 1:
            body += [f"### {t['run']}", "", "```bash", run_hint[1], "```", ""]
    else:
        body += [
            f"### {t['install']}",
            "",
            "```bash",
            "# <your-command>",
            "```",
            "",
        ]
    body += [
        f"## 📁 {t['structure']}",
        "",
        "```text",
        analysis.get("tree", ""),
        "```",
        "",
        f"## 🖼️ {t['screenshot']}",
        "",
        t["screenshot_note"],
        "",
        f"## 🤝 {t['contribute']}",
        "",
        t["contribute_note"],
        "",
        f"## 📄 {t['license']}",
        "",
        f"[{license_name}](LICENSE)",
        "",
    ]
    return "\n".join(body)


def llm_chat(
    system: str,
    user: str,
    cfg: dict,
    max_tokens: int = 4000,
    temperature: float = 0.4,
) -> str:
    api_key = cfg["llm"]["api_key"]
    base_url = cfg["llm"]["base_url"].rstrip("/")
    model = cfg["llm"]["model"]
    if not api_key:
        raise ToolError("未配置 LLM API Key（OPENAI_API_KEY / LLM_API_KEY）")

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    req = request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    last_exc: Exception | None = None
    for attempt in range(2):
        try:
            with open_url(req, timeout=180) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            content = data["choices"][0]["message"]["content"]
            if isinstance(content, list):
                content = "".join(
                    part.get("text", "") for part in content if isinstance(part, dict)
                )
            text = str(content).strip()
            if not text:
                raise ToolError("LLM 返回了空内容")
            return text
        except ToolError:
            raise
        except error.URLError as exc:
            if isinstance(exc.reason, ssl.SSLError):
                last_exc = exc
                log(
                    f"LLM 接口 SSL 证书校验失败: {exc.reason}，"
                    "可在 config.json 中设置 \"verify_ssl\": false 后重试",
                    "WARN",
                )
            else:
                last_exc = exc
                log(
                    f"LLM 接口连接失败: {exc.reason}，"
                    "请检查网络或 config.json 中的 proxy 设置",
                    "WARN",
                )
        except Exception as exc:
            last_exc = exc
            log(f"LLM 调用失败，正在重试: {exc}", "WARN")
    raise ToolError(f"LLM 调用失败: {last_exc}")


def call_llm(analysis: dict, lang: str, cfg: dict) -> str:
    lang_label = "简体中文" if lang == "zh" else "English"
    system = (
        "You are an expert open-source developer and technical writer. "
        "You write polished, accurate GitHub README files that attract users and contributors. "
        "You never invent commands, dependencies, features, or metrics that are not present "
        "in the project analysis. When information is missing, use honest placeholders."
    )
    user = (
        f"请为下面这个项目撰写一份完整、精美、可直接用于 GitHub 的 README.md。\n\n"
        f"输出语言: {lang_label}\n\n"
        f"要求:\n"
        f"1. 结构: 标题 + 一句 slogan；项目简介；功能特性；技术栈（用 shields.io 徽章）；"
        f"快速开始（安装/运行）；项目结构；截图占位；贡献指南；许可证。\n"
        f"2. 只使用分析数据中出现的信息；不确定的命令用 <your-command> 占位，不要编造。\n"
        f"3. Markdown 格式规范，善用表格和代码块，emoji 适量点缀。\n"
        f"4. 只输出 README 的 markdown 正文，不要任何解释，不要用代码围栏包裹整个文档。\n\n"
        f"项目分析数据(JSON):\n{json.dumps(analysis, ensure_ascii=False, indent=2)}"
    )
    return llm_chat(system, user, cfg)


def _ai_repo_about(analysis: dict, cfg: dict) -> str | None:
    """用 AI 生成一句仓库 About 简介；失败返回 None 由调用方回退。"""
    try:
        lang = resolve_lang(analysis, cfg.get("readme_lang", "auto"))
        lang_label = "简体中文" if lang == "zh" else "English"
        compact = {
            "name": analysis["name"],
            "languages": list(analysis.get("languages", {})),
            "metadata": analysis.get("metadata") or {},
            "run_hint": analysis.get("run_hint") or [],
            "total_files": analysis.get("total_files"),
            "total_lines": analysis.get("total_lines"),
        }
        system = "You write concise, attractive GitHub repository descriptions."
        user = (
            f"根据以下项目分析，用{lang_label}为这个 GitHub 仓库写一句简介（About）。"
            "要求：一句话，不超过 100 字，准确概括项目用途和亮点，"
            "不要标题、不要引号、不要任何 markdown 符号。\n\n"
            f"分析数据:\n{json.dumps(compact, ensure_ascii=False, indent=2)}"
        )
        text = llm_chat(system, user, cfg, max_tokens=200, temperature=0.6)
        text = text.strip().strip('"').strip("'").strip()
        return text[:300] or None
    except Exception as exc:
        log(f"AI 生成 About 失败（{exc}），回退到 README slogan", "WARN")
        return None


def generate_readme(analysis: dict, cfg: dict, no_ai: bool = False) -> tuple[str, str]:
    lang = resolve_lang(analysis, cfg.get("readme_lang", "auto"))
    log(f"README 语言: {'简体中文' if lang == 'zh' else 'English'}")
    if no_ai or not cfg["llm"]["api_key"]:
        if not cfg["llm"]["api_key"]:
            log("未配置 LLM API Key，使用内置模板生成 README", "WARN")
        else:
            log("已指定 --no-ai，使用内置模板生成 README", "WARN")
        return fallback_readme(analysis, lang), lang
    try:
        log(f"正在调用 AI 生成 README（模型: {cfg['llm']['model']}）...")
        content = call_llm(analysis, lang, cfg)
        return content, lang
    except Exception as exc:
        log(f"AI 生成失败（{exc}），回退到内置模板", "WARN")
        return fallback_readme(analysis, lang), lang


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------

def write_readme(root: Path, content: str, force: bool, output: str | None, dry_run: bool) -> str:
    target = Path(output) if output else root / "README.md"
    if target.exists() and not force and not output:
        log(f"README 已存在: {target}（使用 --force 覆盖）")
        return "skip"
    if dry_run:
        log(f"将写入 README: {target}")
        return "dry"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    log(f"README 已生成: {target}", "OK")
    return "written"


def print_preview(content: str, max_lines: int = 40) -> None:
    lines = content.splitlines()
    print("\n-------------------- README 预览 --------------------")
    print("\n".join(lines[:max_lines]))
    if len(lines) > max_lines:
        print(f"...（共 {len(lines)} 行，完整内容请查看文件）")
    print("------------------------------------------------------\n")


def process_analyze(root: Path, cfg: dict, args: argparse.Namespace) -> None:
    analysis = analyze_project(root)
    log(f"项目: {analysis['name']}")
    langs = ", ".join(f"{k}({v['files']}个文件)" for k, v in analysis["languages"].items()) or "未知"
    log(f"语言: {langs}")
    log(f"规模: {analysis['total_files']} 个文件 / {analysis['total_lines']:,} 行代码")
    md = analysis.get("metadata")
    if md and (md.get("name") or md.get("description")):
        log(f"元数据: {md.get('name') or ''} - {md.get('description') or ''}")
    if analysis.get("license"):
        log(f"许可证: {analysis['license']}")

    content, lang = generate_readme(analysis, cfg, no_ai=args.no_ai)
    print_preview(content)
    status = write_readme(root, content, args.force, args.output, args.dry_run)
    if status in ("skip", "dry"):
        log(f"处理完成（{status}）")


def ensure_remote(root: Path, analysis: dict, cfg: dict, private: bool, dry_run: bool) -> str:
    r = run_git(root, ["remote", "get-url", "origin"], check=False)
    if r.returncode == 0 and r.stdout.strip():
        url = r.stdout.strip()
        log(f"已存在远程仓库: {url}")
        _update_repo_about(url, root, analysis, cfg, dry_run)
        return url

    name = sanitize_repo_name(root.name)
    description = repo_description(root, analysis)
    if dry_run:
        log(f"将创建 GitHub 仓库: {name} (private={private}) 并添加为 origin")
        return f"https://github.com/<owner>/{name}.git"

    if not cfg["github"]["token"]:
        raise ToolError(
            "项目没有远程仓库，且未配置 GITHUB_TOKEN，无法创建 GitHub 仓库。"
            "请在 config.json 或环境变量中配置 token。"
        )

    owner = gh_get_login(cfg["github"]["token"])
    log(f"GitHub 用户: {owner}")
    repo = gh_create_repo(owner, name, description, private, cfg["github"]["token"])
    if repo.get("conflict"):
        log(f"仓库 {name} 已存在，直接使用现有仓库（不再创建同名新仓库）", "WARN")
        url = f"https://github.com/{owner}/{name}.git"
    else:
        url = repo["clone_url"]
        log(f"已创建远程仓库: {repo['html_url']}", "OK")
    run_git(root, ["remote", "add", "origin", url])
    _update_repo_about(url, root, analysis, cfg, dry_run)
    return url


def _update_repo_about(url: str, root: Path, analysis: dict, cfg: dict, dry_run: bool) -> None:
    """通过 GitHub API 更新仓库 About（描述），不影响推送结果。"""
    parsed = parse_github_url(url)
    token = cfg["github"]["token"]
    if not parsed or not token or dry_run:
        return
    owner, repo = parsed
    desc = _ai_repo_about(analysis, cfg) if cfg["llm"]["api_key"] else None
    if not desc:
        desc = repo_description(root, analysis)
    if not desc:
        return
    try:
        gh_api(
            f"https://api.github.com/repos/{owner}/{repo}",
            token,
            method="PATCH",
            payload={"description": desc[:350]},
        )
        log(f"已更新仓库 About: {desc[:60]}{'…' if len(desc) > 60 else ''}", "OK")
    except Exception as exc:
        log(f"更新仓库 About 失败（不影响推送）: {exc}", "WARN")


def process_push(root: Path, cfg: dict, args: argparse.Namespace) -> None:
    analysis = analyze_project(root)
    ensure_git_repo(root, args.dry_run)
    ensure_identity(root, cfg, args.dry_run)
    commit_changes(root, cfg.get("commit_message", "docs: generate README via GitHub Auto"), args.dry_run)
    ensure_remote(root, analysis, cfg, args.private, args.dry_run)
    push_to_remote(
        root,
        cfg["github"]["token"],
        args.dry_run,
        cfg.get("verify_ssl", True),
        cfg.get("proxy", ""),
    )


def is_project(path: Path) -> bool:
    if (path / ".git").exists():
        return True
    src = 0
    for f in path.iterdir():
        if f.is_file():
            if f.suffix.lower() in LANG_MAP:
                src += 1
            elif f.name.lower() in MANIFESTS:
                return True
    return src >= 2


def discover_projects(root: Path) -> list[Path]:
    projects = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        if child.name in SKIP_DIRS or child.name.startswith("."):
            continue
        try:
            if is_project(child):
                projects.append(child)
        except OSError:
            continue
    return projects


def process_run(root: Path, cfg: dict, args: argparse.Namespace) -> None:
    root = Path(root)
    if not root.exists() or not root.is_dir():
        raise ToolError(f"路径不存在或不是文件夹: {root}")
    projects = discover_projects(root)
    if not projects:
        log(f"在 {root} 下未发现项目（需要 .git、清单文件或至少 2 个源码文件）", "WARN")
        return
    log(f"发现 {len(projects)} 个项目:")
    for p in projects:
        log(f"  - {p.name}")

    results = []
    for project in projects:
        log(f"\n========== 处理项目: {project.name} ==========")
        try:
            analysis = analyze_project(project)
            if getattr(args, "deps", False):
                log("生成缺失的依赖文件…")
                generate_dependency_files(project, analysis, dry_run=False, cfg=cfg)
                analysis = analyze_project(project)  # 重新分析以包含新清单
            content, _ = generate_readme(analysis, cfg, no_ai=args.no_ai)
            status = write_readme(project, content, args.force, None, args.dry_run)
            if not args.skip_push and status != "skip":
                ensure_git_repo(project, args.dry_run)
                ensure_identity(project, cfg, args.dry_run)
                commit_changes(project, cfg.get("commit_message", "docs: generate README via GitHub Auto"), args.dry_run)
                ensure_remote(project, analysis, cfg, args.private, args.dry_run)
                push_to_remote(
                    project,
                    cfg["github"]["token"],
                    args.dry_run,
                    cfg.get("verify_ssl", True),
                    cfg.get("proxy", ""),
                )
            results.append((project.name, "OK"))
        except Exception as exc:
            log(f"处理失败: {exc}", "ERR")
            results.append((project.name, f"FAILED: {exc}"))

    print("\n==================== 汇总 ====================")
    for name, status in results:
        mark = "✅" if status == "OK" else "❌"
        print(f"  {mark} {name}  {status}")
    failed = [s for _, s in results if s != "OK"]
    if failed:
        print(f"\n共 {len(results)} 个项目，{len(failed)} 个失败。")


# --------------------------------------------------------------------------
# 连通性诊断
# --------------------------------------------------------------------------

def cmd_doctor(cfg: dict) -> None:
    print("\n========== GitHub Auto 诊断 ==========")
    log(f"GitHub token: {'已配置' if cfg['github']['token'] else '未配置'}")
    log(f"LLM API Key: {'已配置' if cfg['llm']['api_key'] else '未配置'} | 模型: {cfg['llm']['model']}")
    log(f"LLM Base URL: {cfg['llm']['base_url']}")
    log(f"verify_ssl: {cfg.get('verify_ssl', True)}")
    log(f"工具代理配置: {cfg.get('proxy') or '(跟随系统)'}")

    git_proxy = _git_proxy_info(Path.cwd())
    log(f"git 代理配置: {git_proxy or '无'}")

    log("检查 GitHub API 连通性…")
    try:
        t0 = time.time()
        req = request.Request("https://api.github.com/", headers={"User-Agent": "GitHub-Auto"})
        with open_url(req, timeout=15) as resp:
            log(f"GitHub API 连通: OK（HTTP {resp.status}，{(time.time() - t0) * 1000:.0f} ms）", "OK")
    except Exception as exc:
        log(f"GitHub API 连通: 失败（{exc}）", "ERR")

    log("检查 git → github.com（ls-remote 只读测试）…")
    env = dict(os.environ)
    pairs: list[tuple[str, str]] = []
    proxy = str(cfg.get("proxy", "") or "")
    if proxy == "none":
        pairs = [("http.proxy", ""), ("https.proxy", "")]
    elif proxy:
        pairs = [("http.proxy", proxy), ("https.proxy", proxy)]
    env["GIT_CONFIG_COUNT"] = str(len(pairs))
    for idx, (key, value) in enumerate(pairs):
        env[f"GIT_CONFIG_KEY_{idx}"] = key
        env[f"GIT_CONFIG_VALUE_{idx}"] = value
    try:
        result = subprocess.run(
            ["git", "ls-remote", "https://github.com/octocat/Hello-World.git"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=30,
        )
        if result.returncode == 0:
            log("git → github.com: OK（代理/网络可用）", "OK")
        else:
            detail = (result.stderr or result.stdout).strip().splitlines()
            log(f"git → github.com: 失败（{detail[-1] if detail else '未知错误'}）", "ERR")
            log(
                "提示：若提示 via 127.0.0.1 无法连接，说明 git 代理未运行；"
                "请启动代理软件，或运行 git config --global --unset http.proxy 后重试",
                "WARN",
            )
    except subprocess.TimeoutExpired:
        log("git → github.com: 超时（30 秒）", "ERR")
    except Exception as exc:
        log(f"git → github.com: 失败（{exc}）", "ERR")

    if cfg["llm"]["api_key"]:
        log("检查 LLM 接口连通性…")
        try:
            req = request.Request(
                cfg["llm"]["base_url"].rstrip("/") + "/models",
                headers={"Authorization": f"Bearer {cfg['llm']['api_key']}"},
            )
            with open_url(req, timeout=15) as resp:
                log(f"LLM 接口连通: OK（HTTP {resp.status}）", "OK")
        except error.HTTPError as exc:
            log(f"LLM 接口连通: HTTP {exc.code}（接口可达，可能是鉴权或端点问题）", "WARN")
        except Exception as exc:
            log(f"LLM 接口连通: 失败（{exc}）", "ERR")
    else:
        log("LLM 接口: 未配置 API Key，跳过连通性检查", "WARN")
    print("========== 诊断完成 ==========\n")


# --------------------------------------------------------------------------
# Markdown 渲染（用于 Web 预览，无需第三方库）
# --------------------------------------------------------------------------

def _inline_markdown(text: str) -> str:
    text = html.escape(text, quote=False)
    code_spans: list[str] = []

    def _save_code(m):
        code_spans.append(m.group(1))
        return f"\x00{len(code_spans) - 1}\x00"

    text = re.sub(r"`([^`]+)`", _save_code, text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", text)
    text = re.sub(r"~~([^~]+)~~", r"<del>\1</del>", text)

    def _img(m):
        return f'<img src="{m.group(2)}" alt="{m.group(1)}" loading="lazy">'

    text = re.sub(r"!\[([^\]]*)\]\(([^)\s]+)\)", _img, text)

    def _link(m):
        return f'<a href="{m.group(2)}" target="_blank" rel="noopener">{m.group(1)}</a>'

    text = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", _link, text)

    def _restore_code(m):
        return f"<code>{code_spans[int(m.group(1))]}</code>"

    return re.sub(r"\x00(\d+)\x00", _restore_code, text)


def render_markdown(text: str) -> str:
    """把 Markdown 渲染成 HTML（覆盖 README 常用语法）。"""
    text = text.replace("\r\n", "\n")
    lines = text.split("\n")
    parts: list[str] = []
    n = len(lines)
    i = 0

    while i < n:
        line = lines[i]
        if not line.strip():
            i += 1
            continue

        fence = re.match(r"^```(\w*)\s*$", line)
        if fence:
            lang = fence.group(1)
            buf: list[str] = []
            i += 1
            while i < n and not re.match(r"^```\s*$", lines[i]):
                buf.append(lines[i])
                i += 1
            i += 1
            code = html.escape("\n".join(buf))
            cls = f' class="lang-{html.escape(lang)}"' if lang else ""
            parts.append(f"<pre><code{cls}>{code}</code></pre>")
            continue

        m = re.match(r"^(#{1,6})\s+(.*)", line)
        if m:
            level = len(m.group(1))
            parts.append(f"<h{level}>{_inline_markdown(m.group(2))}</h{level}>")
            i += 1
            continue

        if re.match(r"^\s*([-*_])\s*(\1\s*){2,}\s*$", line):
            parts.append("<hr>")
            i += 1
            continue

        if line.lstrip().startswith(">"):
            buf = []
            while i < n and lines[i].lstrip().startswith(">"):
                buf.append(lines[i].lstrip()[1:].strip())
                i += 1
            parts.append(f"<blockquote>{_inline_markdown(' '.join(buf))}</blockquote>")
            continue

        if (
            "|" in line
            and i + 1 < n
            and "-" in lines[i + 1]
            and re.match(r"^\s*\|?[\s:\-|]+\|?\s*$", lines[i + 1])
        ):
            header = [c.strip() for c in line.strip().strip("|").split("|")]
            i += 2
            rows = []
            while i < n and lines[i].strip() and "|" in lines[i]:
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            table = ["<table><thead><tr>"]
            table += [f"<th>{_inline_markdown(h)}</th>" for h in header]
            table.append("</tr></thead><tbody>")
            for row in rows:
                table.append("<tr>")
                table += [f"<td>{_inline_markdown(c)}</td>" for c in row]
                table.append("</tr>")
            table.append("</tbody></table>")
            parts.append("".join(table))
            continue

        mlist = re.match(r"^\s*([-*+]|\d+\.)\s+(.*)", line)
        if mlist:
            ordered = mlist.group(1)[0].isdigit()
            items = [mlist.group(2)]
            i += 1
            while i < n:
                mm = re.match(r"^\s*([-*+]|\d+\.)\s+(.*)", lines[i])
                if not mm:
                    break
                items.append(mm.group(2))
                i += 1
            tag = "ol" if ordered else "ul"
            parts.append(
                f"<{tag}>" + "".join(f"<li>{_inline_markdown(it)}</li>" for it in items) + f"</{tag}>"
            )
            continue

        buf = [line.strip()]
        i += 1
        while i < n:
            ln = lines[i]
            if (
                not ln.strip()
                or re.match(r"^(#{1,6})\s", ln)
                or re.match(r"^```", ln)
                or ln.lstrip().startswith(">")
            ):
                break
            buf.append(ln.strip())
            i += 1
        parts.append(f"<p>{_inline_markdown(' '.join(buf))}</p>")

    return "\n".join(parts)


# --------------------------------------------------------------------------
# Web 可视化界面
# --------------------------------------------------------------------------

WEBUI_DIR = Path(__file__).resolve().parent / "webui"


class JobManager:
    """在后台线程中执行任务，日志可被前端轮询获取。"""

    def __init__(self, max_jobs: int = 50):
        self._lock = threading.Lock()
        self._jobs: dict = {}
        self._counter = 0
        self._active: str | None = None
        self._max_jobs = max_jobs

    def start(self, name: str, fn) -> str:
        with self._lock:
            if self._active:
                raise ToolError("已有任务正在运行，请等待完成后再试")
            self._counter += 1
            job_id = f"job-{self._counter}"
            job = {
                "id": job_id,
                "name": name,
                "status": "running",
                "logs": [],
                "result": None,
                "error": None,
                "started": time.time(),
            }
            self._jobs[job_id] = job
            self._active = job_id
            self._prune_locked()
        threading.Thread(target=self._run, args=(job_id, name, fn), daemon=True).start()
        return job_id

    def _run(self, job_id: str, name: str, fn) -> None:
        def job_log(msg, level="INFO"):
            self.log(job_id, msg, level)

        prev = _LOGGER
        set_logger(job_log)
        try:
            result = fn(job_log)
            with self._lock:
                job = self._jobs.get(job_id)
                if job:
                    job["status"] = "done"
                    job["result"] = result
        except Exception as exc:
            with self._lock:
                job = self._jobs.get(job_id)
                if job:
                    job["status"] = "error"
                    job["error"] = str(exc)
            self.log(job_id, f"任务失败: {exc}", "ERR")
        finally:
            set_logger(prev)
            with self._lock:
                if self._active == job_id:
                    self._active = None

    def log(self, job_id: str, msg, level: str = "INFO") -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job["logs"].append(
                    {"level": level, "msg": str(msg), "ts": time.strftime("%H:%M:%S")}
                )
                if len(job["logs"]) > 500:
                    job["logs"] = job["logs"][-500:]

    def get(self, job_id: str) -> dict | None:
        with self._lock:
            return self._jobs.get(job_id)

    def _prune_locked(self) -> None:
        ids = sorted(self._jobs, key=lambda k: self._jobs[k]["started"])
        for old in ids[: -self._max_jobs]:
            self._jobs.pop(old, None)


def list_dirs(path: str) -> dict:
    if not path:
        if os.name == "nt":
            roots = [f"{d}:\\" for d in string.ascii_uppercase if Path(f"{d}:\\").exists()]
            return {"path": "", "parent": None, "dirs": roots}
        return {"path": "/", "parent": None, "dirs": ["/"]}
    p = Path(path)
    if not p.exists() or not p.is_dir():
        raise ToolError(f"文件夹不存在: {path}")
    parent = str(p.parent) if str(p.parent) != str(p) else None
    try:
        dirs = sorted((e.name for e in p.iterdir() if e.is_dir()), key=str.lower)
    except OSError as exc:
        raise ToolError(f"无法读取文件夹: {exc}") from exc
    return {"path": str(p), "parent": parent, "dirs": dirs}


def _make_job_generate(cfg: dict, body: dict):
    def fn(log_fn):
        path = Path(str(body.get("path") or ""))
        log_fn(f"开始分析项目: {path}")
        analysis = analyze_project(path)
        langs = ", ".join(list(analysis["languages"])) or "未知"
        log_fn(
            f"{analysis['name']}: {langs} | "
            f"{analysis['total_files']} 个文件 / {analysis['total_lines']} 行"
        )
        if str(body.get("lang") or "") in ("zh", "en"):
            cfg["readme_lang"] = str(body["lang"])
        content, _ = generate_readme(analysis, cfg, no_ai=bool(body.get("no_ai")))
        target = path / "README.md"
        if target.exists() and not bool(body.get("force")):
            log_fn("README 已存在，未覆盖（勾选“强制覆盖已有README”可重新生成）", "WARN")
            return {"status": "skipped", "path": str(target)}
        target.write_text(content, encoding="utf-8")
        log_fn(f"README 已生成: {target}", "OK")
        return {"status": "written", "path": str(target), "preview": content[:3000]}

    return fn


def _make_job_push(cfg: dict, body: dict):
    def fn(log_fn):
        path = Path(str(body.get("path") or ""))
        private = bool(body.get("private"))
        message = (
            str(body.get("commit_message") or "").strip()
            or cfg.get("commit_message", "docs: generate README via GitHub Auto")
        )
        log_fn(f"开始推送项目: {path}")
        analysis = analyze_project(path)
        ensure_git_repo(path, dry_run=False)
        ensure_identity(path, cfg, dry_run=False)
        commit_changes(path, message, dry_run=False)
        ensure_remote(path, analysis, cfg, private, dry_run=False)
        push_to_remote(
            path,
            cfg["github"]["token"],
            dry_run=False,
            verify_ssl=cfg.get("verify_ssl", True),
            proxy=cfg.get("proxy", ""),
        )
        log_fn("推送完成 🎉", "OK")
        return {"status": "pushed"}

    return fn


def _make_job_deps(cfg: dict, body: dict):
    def fn(log_fn):
        path = Path(str(body.get("path") or ""))
        log_fn(f"开始分析项目: {path}")
        analysis = analyze_project(path)
        created = generate_dependency_files(path, analysis, dry_run=False, cfg=cfg)
        if not created:
            log_fn("没有需要创建的依赖文件（均已存在或无第三方依赖）", "WARN")
        else:
            log_fn(f"已创建: {', '.join(created)}", "OK")
        return {"status": "done", "created": created}

    return fn


class WebHandler(BaseHTTPRequestHandler):
    server_version = "GitHubAutoWeb/1.0"
    CFG: dict = {}
    MANAGER: JobManager = None

    def log_message(self, fmt, *args):
        pass

    def _json(self, obj, code: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception as exc:
            raise ToolError(f"请求体不是合法 JSON: {exc}") from exc

    def _send_static(self, path: str) -> None:
        name = path.lstrip("/") or "index.html"
        if name == "favicon.ico":
            self.send_response(204)
            self.end_headers()
            return
        file = (WEBUI_DIR / name).resolve()
        if not str(file).startswith(str(WEBUI_DIR.resolve())) or not file.is_file():
            self.send_error(404)
            return
        ctype = {
            ".html": "text/html; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
        }.get(file.suffix, "application/octet-stream")
        data = file.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _handle_api_get(self, path: str, query: dict) -> None:
        cfg = self.CFG
        if path == "/api/config":
            self._json(
                {
                    "version": VERSION,
                    "llm_key_set": bool(cfg["llm"]["api_key"]),
                    "llm_api_key": _mask_secret(cfg["llm"]["api_key"]),
                    "llm_base_url": cfg["llm"]["base_url"],
                    "llm_model": cfg["llm"]["model"],
                    "github_token_set": bool(cfg["github"]["token"]),
                    "github_token": _mask_secret(cfg["github"]["token"]),
                    "author_name": cfg["github"].get("author_name", ""),
                    "author_email": cfg["github"].get("author_email", ""),
                    "readme_lang": cfg.get("readme_lang", "auto"),
                    "private": bool(cfg.get("private", False)),
                    "commit_message": cfg.get("commit_message", DEFAULT_CONFIG["commit_message"]),
                    "verify_ssl": bool(cfg.get("verify_ssl", True)),
                    "ca_bundle": cfg.get("ca_bundle", ""),
                    "proxy": str(cfg.get("proxy", "") or ""),
                    "license": cfg.get("license", "MIT"),
                }
            )
            return
        if path.startswith("/api/jobs/"):
            job = self.MANAGER.get(path[len("/api/jobs/"):])
            if not job:
                raise ToolError("任务不存在")
            self._json(job)
            return
        if path == "/api/fs":
            self._json(list_dirs((query.get("path") or [""])[0]))
            return
        if path == "/api/analyze":
            proj = (query.get("path") or [""])[0]
            if not proj:
                raise ToolError("缺少 path 参数")
            self._json(analyze_project(Path(proj)))
            return
        if path == "/api/readme":
            proj = (query.get("path") or [""])[0]
            if not proj:
                raise ToolError("缺少 path 参数")
            target = Path(proj) / "README.md"
            self._json(
                {
                    "path": str(target),
                    "exists": target.exists(),
                    "content": _read_text(target) if target.exists() else "",
                }
            )
            return
        raise ToolError(f"未知接口: {path}")

    def _handle_api_post(self, path: str, body: dict) -> None:
        cfg = self.CFG
        if path == "/api/scan":
            root = str(body.get("root") or "").strip()
            if not root:
                raise ToolError("缺少 root 参数")
            root_path = Path(root)
            if not root_path.exists() or not root_path.is_dir():
                raise ToolError(f"文件夹不存在: {root}")
            projects = discover_projects(root_path)
            self._json(
                {
                    "root": root,
                    "projects": [{"name": p.name, "path": str(p)} for p in projects],
                }
            )
            return
        if path == "/api/generate":
            job_id = self.MANAGER.start("generate", _make_job_generate(cfg, body))
            self._json({"job_id": job_id})
            return
        if path == "/api/push":
            job_id = self.MANAGER.start("push", _make_job_push(cfg, body))
            self._json({"job_id": job_id})
            return
        if path == "/api/deps":
            job_id = self.MANAGER.start("deps", _make_job_deps(cfg, body))
            self._json({"job_id": job_id})
            return
        if path == "/api/preview":
            self._json({"html": render_markdown(str(body.get("markdown") or ""))})
            return
        if path == "/api/save":
            root = Path(str(body.get("path") or ""))
            if not root.is_dir():
                raise ToolError("path 必须是项目文件夹")
            content = str(body.get("content") or "")
            target = root / "README.md"
            target.write_text(content, encoding="utf-8")
            self._json({"ok": True, "path": str(target)})
            return
        if path == "/api/config":
            if str(body.get("llm_api_key") or "").strip():
                cfg["llm"]["api_key"] = str(body["llm_api_key"]).strip()
            if str(body.get("llm_model") or "").strip():
                cfg["llm"]["model"] = str(body["llm_model"]).strip()
            if str(body.get("llm_base_url") or "").strip():
                cfg["llm"]["base_url"] = str(body["llm_base_url"]).strip()
            if str(body.get("github_token") or "").strip():
                cfg["github"]["token"] = str(body["github_token"]).strip()
            if isinstance(body.get("author_name"), str):
                cfg["github"]["author_name"] = body["author_name"].strip()
            if isinstance(body.get("author_email"), str):
                cfg["github"]["author_email"] = body["author_email"].strip()
            if body.get("readme_lang") in ("auto", "zh", "en"):
                cfg["readme_lang"] = body["readme_lang"]
            if isinstance(body.get("private"), bool):
                cfg["private"] = body["private"]
            if str(body.get("commit_message") or "").strip():
                cfg["commit_message"] = str(body["commit_message"]).strip()
            if isinstance(body.get("verify_ssl"), bool):
                cfg["verify_ssl"] = body["verify_ssl"]
            if isinstance(body.get("ca_bundle"), str):
                cfg["ca_bundle"] = body["ca_bundle"].strip()
            if isinstance(body.get("proxy"), str):
                cfg["proxy"] = body["proxy"].strip()
            if str(body.get("license") or "").strip():
                cfg["license"] = str(body["license"]).strip()
            configure_ssl(cfg)
            save_config(cfg)
            self._json({"ok": True})
            return
        raise ToolError(f"未知接口: {path}")

    def do_GET(self):
        parsed = urlparse(self.path)
        try:
            if parsed.path.startswith("/api/"):
                self._handle_api_get(parsed.path, parse_qs(parsed.query))
            else:
                self._send_static(parsed.path)
        except ToolError as exc:
            self._json({"error": str(exc)}, 400)
        except Exception as exc:
            self._json({"error": f"服务器错误: {exc}"}, 500)

    def do_POST(self):
        parsed = urlparse(self.path)
        try:
            self._handle_api_post(parsed.path, self._read_json())
        except ToolError as exc:
            self._json({"error": str(exc)}, 400)
        except Exception as exc:
            self._json({"error": f"服务器错误: {exc}"}, 500)


def make_handler(cfg: dict, manager: JobManager):
    class Handler(WebHandler):
        pass

    Handler.CFG = cfg
    Handler.MANAGER = manager
    return Handler


def serve_web(cfg: dict, args: argparse.Namespace) -> None:
    runtime_cfg = deepcopy(cfg)
    manager = JobManager()
    handler = make_handler(runtime_cfg, manager)
    httpd = ThreadingHTTPServer((args.host, args.port), handler)
    url = f"http://{args.host}:{args.port}/"
    log(f"GitHub Auto Web 界面已启动: {url}")
    log("按 Ctrl+C 停止服务")
    if not args.no_browser:
        threading.Thread(target=lambda: (time.sleep(1.2), webbrowser.open(url)), daemon=True).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log("服务已停止", "WARN")
    finally:
        httpd.server_close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="github_auto",
        description="AI 工具：自动分析项目、生成精美 GitHub README 并推送代码。",
    )
    parser.add_argument("--config", help="指定配置文件路径（默认读取脚本目录或当前目录下的 config.json）")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")

    sub = parser.add_subparsers(dest="command", required=True)

    p_analyze = sub.add_parser("analyze", help="分析单个项目并生成 README")
    p_analyze.add_argument("path", help="项目路径")
    p_analyze.add_argument("--lang", choices=["auto", "zh", "en"], default=None, help="README 语言")
    p_analyze.add_argument("--force", action="store_true", help="覆盖已有 README")
    p_analyze.add_argument("--output", default=None, help="README 输出路径（默认写入项目根目录）")
    p_analyze.add_argument("--no-ai", action="store_true", help="跳过 AI，使用内置模板")
    p_analyze.add_argument("--dry-run", action="store_true", help="只打印将要执行的操作，不写文件、不调用 API")

    p_run = sub.add_parser("run", help="批量处理根目录下的所有项目（生成 README 并推送）")
    p_run.add_argument("root", help="包含多个项目的根目录")
    p_run.add_argument("--lang", choices=["auto", "zh", "en"], default=None, help="README 语言")
    p_run.add_argument("--force", action="store_true", help="覆盖已有 README")
    p_run.add_argument("--private", action="store_true", help="创建私有仓库")
    p_run.add_argument("--skip-push", action="store_true", help="只生成 README，不推送")
    p_run.add_argument("--no-ai", action="store_true", help="跳过 AI，使用内置模板")
    p_run.add_argument("--deps", action="store_true", help="先自动补全依赖文件，再生成 README")
    p_run.add_argument("--dry-run", action="store_true", help="只打印将要执行的操作，不写文件、不调用 API、不推送")

    p_push = sub.add_parser("push", help="将项目提交并推送到 GitHub（无远程时自动创建仓库）")
    p_push.add_argument("path", help="项目路径")
    p_push.add_argument("--private", action="store_true", help="创建私有仓库")
    p_push.add_argument("--dry-run", action="store_true", help="只打印将要执行的操作，不实际推送")

    p_web = sub.add_parser("web", help="启动可视化 Web 界面（推荐）")
    p_web.add_argument("--host", default="127.0.0.1", help="监听地址（默认 127.0.0.1）")
    p_web.add_argument("--port", type=int, default=8765, help="端口（默认 8765）")
    p_web.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")

    p_doctor = sub.add_parser("doctor", help="诊断网络/代理/配置连通性")

    p_deps = sub.add_parser("deps", help="自动创建缺失的项目依赖文件（requirements.txt / package.json / go.mod / Cargo.toml / .gitignore / LICENSE）")
    p_deps.add_argument("path", help="项目路径")
    p_deps.add_argument("--force", action="store_true", help="覆盖已存在的清单/配置文件（不覆盖源码）")
    p_deps.add_argument("--dry-run", action="store_true", help="只预览将要创建的文件")

    return parser


def main() -> int:
    args = build_parser().parse_args()
    cfg = load_config(args.config)
    configure_ssl(cfg)

    if args.command == "analyze":
        if args.lang:
            cfg["readme_lang"] = args.lang
        process_analyze(Path(args.path), cfg, args)
    elif args.command == "push":
        process_push(Path(args.path), cfg, args)
    elif args.command == "run":
        if args.lang:
            cfg["readme_lang"] = args.lang
        process_run(Path(args.root), cfg, args)
    elif args.command == "web":
        serve_web(cfg, args)
    elif args.command == "doctor":
        cmd_doctor(cfg)
    elif args.command == "deps":
        root = Path(args.path)
        if not root.exists() or not root.is_dir():
            raise ToolError(f"路径不存在或不是文件夹: {root}")
        analysis = analyze_project(root)
        generate_dependency_files(root, analysis, force=args.force, dry_run=args.dry_run, cfg=cfg)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ToolError as exc:
        log(str(exc), "ERR")
        sys.exit(1)
    except KeyboardInterrupt:
        log("已取消", "WARN")
        sys.exit(130)
