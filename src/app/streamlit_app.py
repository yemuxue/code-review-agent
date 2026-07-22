"""
Code Review Agent — Streamlit Frontend
Claude Code / Codex UI | Bilingual CN+EN | Multi-Agent
"""
from __future__ import annotations
import sys, io, os, time, datetime, json, uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
PROJECT_ROOT = Path(__file__).parent.parent.parent
LOGS_DIR = str(PROJECT_ROOT / "logs")
REPORTS_DIR = str(PROJECT_ROOT / "reports")
SESSIONS_DIR = str(PROJECT_ROOT / "sessions")
os.makedirs(LOGS_DIR, exist_ok=True)
os.makedirs(REPORTS_DIR, exist_ok=True)
os.makedirs(SESSIONS_DIR, exist_ok=True)

import streamlit as st
from src.llm_client import AnthropicClient
from src.harness.agent import AgentHarness, ToolDefinition
from src.harness.telemetry import AgentLogger
from src.harness.auth import HumanInTheLoop
from src.model_router import ModelRouter
from src.multi_agent.langgraph_orchestrator import LangGraphOrchestrator as Orchestrator
from src.tools.git_tools import list_files, read_file, grep_pattern, run_command
from src.storage.database import Database, Session as DBSession
from src.memory.vector_store import VectorStore, FindingDocument

# ═══════════════════════════════════════
# Page Config
# ═══════════════════════════════════════

st.set_page_config(page_title="Code Review Agent", page_icon="⚙️", layout="wide",
                   initial_sidebar_state="expanded",
                   menu_items={"Get Help": None, "Report a bug": None, "About": "Multi-Agent Code Analysis"})

# ═══════════════════════════════════════
# CSS — Claude Code / GitHub Dark
# ═══════════════════════════════════════

st.markdown("""<style>
    .stApp { background: #0d1117; }
    .main .block-container { padding: 1rem 2rem; max-width: 960px; }
    section[data-testid="stSidebar"] { background: #161b22; border-right: 1px solid #30363d; }
    section[data-testid="stSidebar"] .block-container { padding: 1rem; }
    h1,h2,h3 { color: #e6edf3 !important; font-weight:600 !important; }
    p,li,label,span { color: #c9d1d9 !important; }
    code { font-family:'Cascadia Code','Fira Code',monospace; background:#161b22; color:#ffa657; padding:2px 6px; border-radius:4px; font-size:13px; }
    pre { background:#161b22; border:1px solid #30363d; border-radius:8px; padding:14px; }
    .metric-row { display:flex; gap:10px; margin:8px 0; flex-wrap:wrap; }
    .metric-card { background:#161b22; border:1px solid #30363d; border-radius:8px; padding:10px 14px; text-align:center; min-width:80px; flex:1; }
    .metric-card .value { font-size:20px; font-weight:700; color:#58a6ff; }
    .metric-card .label { font-size:10px; color:#8b949e; text-transform:uppercase; letter-spacing:.3px; }
    .stButton>button { background:#21262d; color:#c9d1d9; border:1px solid #30363d; border-radius:6px; font-weight:500; }
    .stButton>button:hover { background:#30363d; border-color:#8b949e; }
    .stTextInput>div>div>input, .stTextArea>div>div>textarea { background:#0d1117; border:1px solid #30363d; color:#c9d1d9; border-radius:6px; }
    .stTextInput>div>div>input:focus { border-color:#58a6ff; box-shadow:0 0 0 2px #1f6feb44; }
    .stExpander { background:#161b22; border:1px solid #30363d; border-radius:8px; }
    .stDeployButton { display:none; }
    .file-chip { display:inline-block; background:#1f6feb22; color:#58a6ff; padding:3px 10px; border-radius:12px; font-size:12px; margin:2px 4px; border:1px solid #1f6feb44; }
    .file-chip-remove { color:#f85149; cursor:pointer; margin-left:6px; font-weight:700; }
    .sidebar-section { margin-bottom:14px; }
    .sidebar-section h4 { color:#e6edf3; font-size:12px; font-weight:600; text-transform:uppercase; letter-spacing:.5px; margin-bottom:4px; }
    .log-line { font-family:'Cascadia Code',monospace; font-size:10px; color:#8b949e; white-space:pre-wrap; word-break:break-all; border-bottom:1px solid #21262d; padding:2px 0; }
    .stChatMessage { background:transparent !important; }
</style>""", unsafe_allow_html=True)

# ═══════════════════════════════════════
# Session State
# ═══════════════════════════════════════

# Session defaults
for k, v in {
    "messages": [], "selected_files": [], "current_project": "X:/VScode/code-review-agent/src",
    "session_id": None, "sessions": {},  # session_id -> {name, messages, mode}
}.items():
    if k not in st.session_state: st.session_state[k] = v

# Load saved sessions from disk
# Init persistence layers
db = Database("./data/code_review.db")
vs = VectorStore("./data/search.db")

def _load_sessions():
    sessions = {}
    try:
        for s in db.list_sessions(20):
            sessions[s["id"]] = {
                "name": s.get("name", ""), "mode": s.get("mode", ""),
                "messages": db.get_messages(s["id"]),
                "time": s.get("updated_at", ""),
            }
    except Exception:
        pass
    return sessions

def _save_session(sid: str, data: dict):
    try:
        db.update_session(sid, name=data.get("name", "")[:60],
                          mode=data.get("mode", ""))
    except Exception:
        pass

# Always refresh from DB on every render
st.session_state.sessions = _load_sessions()

# ═══════════════════════════════════════
# Tools
# ═══════════════════════════════════════

TOOLS = [
    ToolDefinition("list_files","List files recursively / 递归列出文件",
        {"type":"object","properties":{"path":{"type":"string","description":"Directory path / 目录路径"},"pattern":{"type":"string"}},"required":["path"]}, list_files),
    ToolDefinition("read_file","Read file content / 读取文件. Use file_path (absolute), start_line, end_line. Do NOT use 'offset'.",
        {"type":"object","properties":{"file_path":{"type":"string","description":"Absolute path"},"start_line":{"type":"integer","description":"Starting line number (1-indexed)"},"end_line":{"type":"integer","description":"Ending line number"}},"required":["file_path"]}, read_file),
    ToolDefinition("grep_pattern","Search regex pattern / 正则搜索代码",
        {"type":"object","properties":{"pattern":{"type":"string","description":"Regex"},"path":{"type":"string"},"file_glob":{"type":"string"}},"required":["pattern","path"]}, grep_pattern),
    ToolDefinition("run_command","Run shell command / 运行命令",
        {"type":"object","properties":{"command":{"type":"string"}},"required":["command"]}, run_command),
]

SYSTEM_PROMPTS = {
    "🔍 Code Review (Single Agent)": """You are a code analysis agent. Read files and find bugs, security, performance, style issues.
Output EVERY finding as: FINDING|file|line|CATEGORY|severity|EN:desc|CN:描述|fix
Be specific. Use real line numbers. Bilingual EN+CN required.""",

    "🧠 Multi-Agent Analysis": """You are a Planner agent. Read files and find ALL issues aggressively.
Output: FINDING|file|line|CATEGORY|severity|EN:desc|CN:描述|fix
Each file must have at least 2 FINDING lines. Bilingual EN+CN required.""",
}

# ═══════════════════════════════════════
# SIDEBAR — Mode, Settings, LOGS
# ═══════════════════════════════════════

with st.sidebar:
    # ── Logo ──
    st.markdown("""<div style="display:flex;align-items:center;gap:8px;margin-bottom:10px;">
        <div style="width:30px;height:30px;background:#1f6feb;border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:16px;">⚙️</div>
        <div><div style="color:#e6edf3;font-weight:700;font-size:15px;">Code Review</div>
        <div style="color:#8b949e;font-size:10px;">Agent Analysis</div></div>
    </div>""", unsafe_allow_html=True)

    # ── Mode (must be before New Chat to define `mode`) ──
    st.markdown('<div class="sidebar-section"><h4>Mode / 分析模式</h4></div>', unsafe_allow_html=True)
    mode = st.selectbox("mode", list(SYSTEM_PROMPTS.keys()), label_visibility="collapsed")
    is_multi = "Multi-Agent" in mode
    if is_multi:
        st.caption("🧠 Planner → Executor → Reviewer")
    else:
        st.caption("🔍 快速代码分析")

    st.divider()

    # ── New Chat ──
    if st.button("➕ New Chat / 新建对话", use_container_width=True):
        if st.session_state.messages:
            try:
                sid = st.session_state.session_id or f"chat_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"
                name = (st.session_state.messages[0].get("content","New Chat").split("\n")[0][:50]
                        .replace("\n", " ").replace("|", ""))
                _save_session(sid, {"name": name, "messages": st.session_state.messages,
                                    "mode": mode, "time": datetime.datetime.now().isoformat()})
                st.session_state.sessions = _load_sessions()
            except Exception:
                pass
        st.session_state.messages = []
        st.session_state.selected_files = []
        st.session_state.session_id = db.create_session(name="New Chat", mode=mode)
        st.rerun()

    st.divider()

    # ── Session History / 对话记录 ──
    st.markdown('<div class="sidebar-section"><h4>History / 历史对话</h4></div>', unsafe_allow_html=True)
    if st.button("🔄 Refresh / 刷新", use_container_width=True):
        st.session_state.sessions = _load_sessions(); st.rerun()

    sessions = st.session_state.sessions
    if sessions:
        for sid, data in list(sessions.items())[:10]:
            name = data.get("name", "Untitled")[:30]
            t = data.get("time", "")[:16]
            msgs = data.get("messages", [])
            complete = len(msgs) >= 2  # 有 user + assistant 才算完整
            icon = "✅" if complete else "⏳"
            mode_icon = "M" if "Multi" in data.get("mode", "") else "S"
            c1, c2 = st.columns([8, 1])
            with c1:
                if st.button(f"{icon} {mode_icon} | {name} [{len(msgs)}]\n`{t}`", key=f"h_{sid}", use_container_width=True):
                    st.session_state.messages = list(msgs)
                    st.session_state.session_id = sid
                    st.rerun()
            with c2:
                if st.button("✕", key=f"del_{sid}", help="Delete session"):
                    db.delete_session(sid)
                    st.session_state.sessions = _load_sessions()
                    st.rerun()
    else:
        st.caption("No saved conversations / 暂无保存")

    st.divider()

    # ── Logs ──
    st.divider()
    st.markdown('<div class="sidebar-section"><h4>🔍 Search Findings / 搜索发现</h4></div>', unsafe_allow_html=True)
    search_query = st.text_input("Search", placeholder="e.g. SQL injection, null pointer...", label_visibility="collapsed", key="search_box")
    if search_query:
        results = vs.search(search_query, n_results=5)
        if results:
            for r in results:
                fp = r.get("file_path", "?")
                cat = r.get("category", "?")
                sev = r.get("severity", "?")
                desc = (r.get("description_en") or r.get("description_cn") or "")[:150]
                # Clean markdown: remove ###, **, `, --, numbers
                import re as _re2
                desc_clean = _re2.sub(r'^#+\s*\d+\.\s*\[?\w*\]?\s*\w*\s*--\s*', '', desc)
                desc_clean = desc_clean.replace("**","").replace("`","").strip()[:120]
                icon = {"BUG":"🐛","SECURITY":"🔒","PERF":"⚡","STYLE":"📝","ANALYSIS":"📊"}.get(cat,"📄")
                st.markdown(
                    f"<div style='font-size:11px;padding:5px 0;border-bottom:1px solid #21262d;'>"
                    f"{icon} <b>{fp}</b>"
                    f"<br><span style='color:#8b949e;'>{desc_clean}</span></div>",
                    unsafe_allow_html=True)
        else:
            st.caption("No results / 无结果")
        st.caption(f"{vs.count()} documents indexed / 已索引")

    st.divider()
    st.markdown('<div class="sidebar-section"><h4>📋 Logs / 日志</h4></div>', unsafe_allow_html=True)
    # 从数据库读取当前会话日志
    if st.session_state.session_id:
        log_entries = db.get_logs(st.session_state.session_id, limit=15)
        if log_entries:
            for entry in log_entries:
                icon = "❌" if entry["error"] else ("🔧" if entry["tool"] else "🔄")
                tool_info = f" | {entry['tool']}" if entry["tool"] else ""
                msg = entry.get("message", "")[:40]
                st.markdown(
                    f"<div class='log-line'>"
                    f"{icon} [{entry['event']}] t={entry['turn']}{tool_info}<br>"
                    f"<span style='font-size:9px;color:#484f58;'>{msg}</span></div>",
                    unsafe_allow_html=True)
        else:
            st.caption("No logs for this session / 当前会话无日志")
    else:
        # 显示最近的 JSONL 日志文件列表（全局视图）
        log_dir = Path(LOGS_DIR)
        if log_dir.exists():
            log_files = sorted(log_dir.glob("*.jsonl"), reverse=True)[:3]
            for lf in log_files:
                with st.expander(f"📄 {lf.name}", expanded=False):
                    try:
                        for line in lf.read_text(encoding="utf-8").strip().split("\n")[-8:]:
                            try:
                                rec = json.loads(line)
                                icon = "🔧" if rec.get("tool") else "🔄"
                                st.markdown(
                                    f"<div class='log-line'>"
                                    f"{icon} [{rec.get('event','')}] t={rec.get('turn','')} "
                                    f"{rec.get('tool','')[:20]}</div>",
                                    unsafe_allow_html=True)
                            except: pass
                    except: st.caption("unavailable")
        else:
            st.caption("No logs / 暂无日志")

# ═══════════════════════════════════════
# MAIN AREA — Chat
# ═══════════════════════════════════════

st.markdown("""<div style="display:flex;align-items:center;gap:10px;margin-bottom:4px;">
    <h2 style="margin:0;">Code Review Agent</h2>
    <span style="background:#1f6feb22;color:#58a6ff;padding:2px 10px;border-radius:12px;font-size:11px;font-weight:600;">MULTI-AGENT</span>
</div>""", unsafe_allow_html=True)
st.caption("🧠 LangGraph: plan → execute → review" if is_multi else "🔍 Single Agent Analysis")

# Chat history
for msg in st.session_state.messages:
    role = msg["role"]
    label = "YOU" if role == "user" else "AGENT"
    color = "#8b949e" if role == "user" else "#58a6ff"
    with st.chat_message(role):
        st.markdown(f'<span style="color:{color};font-size:10px;font-weight:700;letter-spacing:.5px;">{label}</span>', unsafe_allow_html=True)
        st.markdown(msg["content"])
        if msg.get("stats"):
            s = msg["stats"]
            st.markdown(f"""<div class="metric-row">
                <div class="metric-card"><div class="value">{s.get('turns_taken','?')}</div><div class="label">Turns</div></div>
                <div class="metric-card"><div class="value">{s.get('tools_called','?')}</div><div class="label">Tools</div></div>
                <div class="metric-card"><div class="value">{s.get('messages_count','?')}</div><div class="label">Msgs</div></div>
            </div>""", unsafe_allow_html=True)
            if msg.get("report_path"): st.caption(f"📄 {msg['report_path']}")

# ═══════════════════════════════════════
# INPUT AREA — File chips + 📎 + chat_input
# ═══════════════════════════════════════

st.divider()

# File upload + chips (simplified, no dynamic key)
if "_seen_file_ids" not in st.session_state:
    st.session_state._seen_file_ids = set()

uploaded = st.file_uploader(
    "📎 Attach files / 添加文件", label_visibility="visible",
    type=["py","md","txt","json","yml","yaml","js","ts","jsx","tsx"],
    accept_multiple_files=True, key="main_uploader",
)

if uploaded:
    import tempfile
    upload_dir = Path(tempfile.gettempdir()) / "code_review_uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    for f in uploaded:
        if f.file_id not in st.session_state._seen_file_ids:
            dest = upload_dir / f.name
            dest.write_bytes(f.getvalue())
            real_path = str(dest.absolute())
            if real_path not in st.session_state.selected_files:
                st.session_state.selected_files.append(real_path)
            st.session_state._seen_file_ids.add(f.file_id)

# Show chips with remove buttons
if st.session_state.selected_files:
    files = list(st.session_state.selected_files)
    # 显示 chips
    chips_html = " ".join(f'<span class="file-chip">📄 {Path(f).name[:20]}</span>' for f in files)
    st.markdown(f'<div style="margin-bottom:4px;">{chips_html}</div>', unsafe_allow_html=True)
    # 删除按钮
    cols = st.columns(min(len(files) + 1, 8))
    for i, fp in enumerate(files[:7]):
        with cols[i]:
            if st.button(f"✕ {Path(fp).name[:12]}", key=f"del_{i}", use_container_width=True):
                st.session_state.selected_files = [x for x in st.session_state.selected_files if x != fp]
                st.rerun()
    if len(files) > 1:
        with cols[-1]:
            if st.button("🗑️ All", key="clear_files", use_container_width=True):
                st.session_state.selected_files = []
                st.session_state._seen_file_ids = set()
                st.rerun()

# Chat input
prompt = st.chat_input("Ask me to analyze code, find bugs, review files... / 输入分析任务")

if prompt:
    # Build query
    extra = ""
    if st.session_state.selected_files:
        files_str = "\n".join(f"- {f}" for f in st.session_state.selected_files)
        extra = (f"\n\nCRITICAL: ONLY analyze these specific files, NOT the whole project:\n{files_str}"
                 f"\nDo NOT read or list other files. Focus exclusively on the files listed above.")

    full_query = prompt + extra
    st.session_state.messages.append({"role": "user", "content": full_query})

    # 立即保存到数据库（防止中断丢失）
    try:
        if not st.session_state.session_id:
            st.session_state.session_id = db.create_session(name=prompt[:50], mode=mode)
        db.add_message(st.session_state.session_id, "user", full_query)
    except Exception:
        pass

    with st.chat_message("user"):
        st.markdown(f'<span style="color:#8b949e;font-size:10px;font-weight:700;">YOU</span>', unsafe_allow_html=True)
        st.markdown(prompt)
        if st.session_state.selected_files:
            chips = "".join(f'<span class="file-chip">📄 {Path(f).name}</span>' for f in st.session_state.selected_files)
            st.markdown(f'<div style="margin-top:4px;">{chips}</div>', unsafe_allow_html=True)

    # Run
    with st.chat_message("assistant"):
        st.markdown(f'<span style="color:#58a6ff;font-size:10px;font-weight:700;">AGENT</span>', unsafe_allow_html=True)

        target = full_query
        mode_label = "MultiAgent" if is_multi else "SingleAgent"

        logger = AgentLogger(LOGS_DIR)
        log_path = logger.log_path
        client = ModelRouter().route(target)  # Auto-select model
        hitl_guard = HumanInTheLoop(auto_approve_safe=True)

        with st.status("🔄 Working...", expanded=True) as status:
            if is_multi:
                status.write("**LangGraph: plan → execute → review**")
                try:
                    orch = Orchestrator(client, TOOLS)
                    lang_result = orch.run(task=target, project_path=st.session_state.current_project)
                    result_text = "\n".join(lang_result.get("messages", ["No findings"]))
                    n_findings = len(lang_result.get("findings", []))
                    if n_findings:
                        result_text += f"\n\n**Findings**: {n_findings} issues found"
                    status.write(f"🧠 LangGraph: plan -> execute -> review")
                    status.write(f"📊 Findings: {n_findings} | Complete: {lang_result.get('complete', False)}")
                    status.update(label="✅ LangGraph Complete", state="complete")
                    stats = {"turns_taken": f"{n_findings} findings", "tools_called": 0, "messages_count": len(lang_result.get("messages", []))}
                except Exception as ex:
                    import traceback
                    result_text = f"❌ Error:\n```\n{traceback.format_exc()}\n```"
                    stats = {"turns_taken": "ERR", "tools_called": 0, "messages_count": 0}
                    status.update(label="❌ Failed", state="error")
            else:
                # ─── Single Agent with Streaming ───
                agent = AgentHarness(model=client, tools=TOOLS, system_prompt=SYSTEM_PROMPTS[mode],
                                     max_turns=8, logger=logger)
                agent.hitl = hitl_guard  # Wire HITL into tool execution
                placeholder = st.empty()
                buffer = [""]  # use list for mutable capture in closure

                async def _stream():
                    async for event in agent.run_streaming(target):
                        if event["type"] == "text_chunk":
                            buffer[0] += event["text"]
                            placeholder.markdown(buffer[0] + " ▌")
                        elif event["type"] == "tool_call_detected":
                            status.write(f"🔧 Calling: {event['name']}")
                        elif event["type"] == "finished":
                            placeholder.markdown(buffer[0])
                    return buffer[0]

                import asyncio
                result_text = asyncio.run(_stream())
                stats = agent.get_stats()
                status.update(label=f"✅ Done — {stats['tools_called']} tools, {stats['turns_taken']} turns", state="complete")

        if not is_multi:
            pass  # streaming already rendered
        else:
            st.markdown(result_text)

        st.markdown(f"""<div class="metric-row">
            <div class="metric-card"><div class="value">{stats.get('turns_taken','?')}</div><div class="label">Turns</div></div>
            <div class="metric-card"><div class="value">{stats.get('tools_called','?')}</div><div class="label">Tools</div></div>
            <div class="metric-card"><div class="value">{stats.get('messages_count','?')}</div><div class="label">Messages</div></div>
        </div>""", unsafe_allow_html=True)

    # Save report
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = f"{REPORTS_DIR}/report_{ts}.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(f"# {mode_label} Report\n\n**Project**: `{st.session_state.current_project}`\n**Time**: {ts}\n**Mode**: {mode}\n\n---\n\n{result_text}")

    st.session_state.messages.append({
        "role": "assistant", "content": result_text, "stats": stats,
        "report_path": report_path,
    })

    # 立即保存助手回复到数据库（防止中断丢失）
    try:
        db.add_message(st.session_state.session_id or "pending", "assistant", result_text, stats, report_path)
    except Exception:
        pass

    # Auto-save to database (full sync)
    try:
        if not st.session_state.session_id:
            st.session_state.session_id = db.create_session(name="New Chat", mode=mode)
        sid = st.session_state.session_id
        name = (st.session_state.messages[0].get("content","New Chat").split("\n")[0][:50]
                .replace("\n", " ").replace("|", ""))
        db.update_session(sid, name=name, mode=mode)
        # Sync messages to DB
        existing = [m["content"] for m in db.get_messages(sid)]
        for msg in st.session_state.messages:
            content = msg.get("content", "")
            if content not in existing:
                db.add_message(sid, msg["role"], content,
                               stats=msg.get("stats"), report_path=msg.get("report_path", ""))
        # Sync logs from JSONL to DB
        try:
            if log_path and os.path.exists(log_path):
                for line in Path(log_path).read_text(encoding="utf-8").strip().split("\n")[-50:]:
                    rec = json.loads(line)
                    db.add_log(sid, rec.get("event",""), rec.get("turn",0),
                               rec.get("tool",""), rec.get("error",False),
                               rec.get("message", rec.get("result_preview","")[:500]))
        except Exception:
            pass
        # Index analysis text as individual paragraphs for granular search
        try:
            import re as _re
            if result_text:
                # Split into paragraphs, index each one separately
                paragraphs = [p.strip() for p in result_text.split("\n\n") if len(p.strip()) > 20]
                for para in paragraphs[:50]:  # max 50 paragraphs
                    # Extract file name from paragraph if present
                    file_match = _re.search(r'`?([\w/\.-]+\.py)`?', para)
                    fp = file_match.group(1) if file_match else "analysis"
                    line_match = _re.search(r':(\d+)', para)
                    ln = int(line_match.group(1)) if line_match else 0
                    vs.add_finding(FindingDocument(
                        file_path=fp, line=ln, category="FINDING", severity="N/A",
                        description_en=para[:2000], description_cn=para[:2000],
                        suggestion="", session_id=sid, verified=True))
                # Also save file:line refs to DB
                for m in _re.finditer(r'`?([\w/\.-]+\.py):(\d+)`?', result_text):
                    fp, ln = m.group(1), int(m.group(2))
                    if len(ln) >= 1 and not fp.startswith(('http','data')):
                        db.add_finding(sid, Finding(id=str(uuid.uuid4()), session_id=sid,
                            file_path=fp, line=ln, category="REF", severity="N/A",
                            description_en="see report", description_cn="见报告",
                            suggestion="", verdict="CONFIRMED"))
        except Exception: pass
        st.session_state.sessions = _load_sessions()
    except Exception:
        pass

    st.rerun()
