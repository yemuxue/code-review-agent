"""LangGraph 编排器技能注入测试：零回归守护 / 角色门 / 每次 run 重算 / 工厂 env。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.llm_client import LLMResponse
from src.multi_agent.agents import (
    EXECUTOR_SYSTEM_PROMPT,
    FIXER_SYSTEM_PROMPT,
    PLANNER_SYSTEM_PROMPT,
    REVIEWER_SYSTEM_PROMPT,
)
from src.multi_agent.factory import create_langgraph_orchestrator
from src.multi_agent.langgraph_orchestrator import LangGraphOrchestrator
from src.skills import load_skills
from src.skills.selector import INDEX_HEADER


class RecordingMockLLM:
    """离线 mock：记录每次 chat 的系统提示词；按用户消息内容路由回复。

    planner 收到带 finding 的回复 → 全链路离线跑通 plan/execute/review 三个节点。
    """

    def __init__(self):
        self.calls = []
        self.planner_prompts = []  # 每次 run 的 planner system prompt（按用户消息标记路由定位）

    def chat(self, messages, tools=None, **kwargs):
        self.calls.append(list(messages))
        user_content = str(messages[-1]["content"])
        if user_content.startswith("Task: "):
            self.planner_prompts.append(messages[0]["content"])
            content = ("FINDING|src/target.py|42|BUG|High|EN: possible None|CN: 可能为None|add check\n"
                       "analysis done")
        elif "Verify finding #" in user_content:
            content = "VERDICT|1|CONFIRMED|line 42: no null check\n"
        else:
            content = "# Code Analysis Report\nreview conclusion, 1 finding"
        return LLMResponse(content=content)


def _write_skill(tmp_path, dir_name="demo", roles="[executor, reviewer, fixer]",
                 triggers="[injection, 注入]", body="No raw SQL\n用参数化查询"):
    skill_dir = tmp_path / dir_name
    skill_dir.mkdir(parents=True)
    content = (
        "---\nname: demo-sec\n"
        f"description: security rules\nroles: {roles}\ntriggers: {triggers}\n"
        "---\n" + body + "\n"
    )
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
    return tmp_path


def _first_system_prompt(mock) -> str:
    """当前 run 的 planner system prompt（按用户消息标记路由定位，跨 run 不漂移）。"""
    return mock.planner_prompts[-1]


def test_no_skills_dir_is_byte_identical_noop(tmp_path):
    """无技能目录 → planner 收到的 system prompt 与改造前逐字节一致（零回归守护）。"""
    mock = RecordingMockLLM()
    orch = LangGraphOrchestrator(mock, [], skills_dir=str(tmp_path / "missing"))
    orch.run(task="check sql injection in login", project_path=str(tmp_path))
    assert _first_system_prompt(mock) == PLANNER_SYSTEM_PROMPT


def test_zero_injection_outside_run(tmp_path):
    """run() 之外直接 _make_agent(role=...) → _role_blocks 为空，零注入。"""
    skills_dir = _write_skill(tmp_path, roles="[executor]", triggers="[injection]")
    orch = LangGraphOrchestrator(object(), [], skills_dir=str(skills_dir))
    agent = orch._make_agent(["read_file"], EXECUTOR_SYSTEM_PROMPT, 4, role="executor")
    assert agent.system_prompt == EXECUTOR_SYSTEM_PROMPT


def test_role_gate_and_injection_through_real_chain(tmp_path):
    """roles 排除 planner：run 全程 planner 提示词不变；executor/reviewer 收到技能正文。"""
    skills_dir = _write_skill(tmp_path, roles="[executor, reviewer, fixer]",
                              triggers="[injection]")
    mock = RecordingMockLLM()
    orch = LangGraphOrchestrator(mock, [], skills_dir=str(skills_dir))
    orch.run(task="check sql injection in login", project_path=str(tmp_path))

    # 节点调用顺序：plan → execute_one → review
    assert len(mock.calls) == 3
    assert _first_system_prompt(mock) == PLANNER_SYSTEM_PROMPT
    executor_prompt = mock.calls[1][0]["content"]
    assert executor_prompt.startswith(EXECUTOR_SYSTEM_PROMPT)
    assert INDEX_HEADER in executor_prompt
    assert "## Skill: demo-sec" in executor_prompt
    assert "No raw SQL\n用参数化查询" in executor_prompt
    reviewer_prompt = mock.calls[2][0]["content"]
    assert reviewer_prompt.startswith(REVIEWER_SYSTEM_PROMPT)
    assert "用参数化查询" in reviewer_prompt

    # run() 结束后 _role_blocks 残留（下次 run 开头重算）→ fixer 可直接断言
    fixer = orch._make_agent(["read_file", "write_file"], FIXER_SYSTEM_PROMPT, 10,
                             role="fixer")
    assert "## Skill: demo-sec" in fixer.system_prompt
    # 无 auto_fix → fixer 节点从未真正执行，确认其提示词来自同一 _role_blocks
    assert "demo-sec" in orch._role_blocks["fixer"]


def test_injection_reset_between_runs_on_same_instance(tmp_path):
    """同一实例二次 run（不同 task）：命中 → 正文注入；不命中 → 精确回退到只含索引。"""
    skills_dir = _write_skill(tmp_path, roles="[executor, reviewer, fixer]",
                              triggers="[injection]")
    mock = RecordingMockLLM()
    orch = LangGraphOrchestrator(mock, [], skills_dir=str(skills_dir))

    orch.run(task="audit sql injection paths", project_path=str(tmp_path))
    assert "## Skill: demo-sec" in orch._role_blocks["executor"]

    orch.run(task="rename variables for readability", project_path=str(tmp_path))
    executor_block = orch._role_blocks["executor"]
    assert "## Skill: demo-sec" not in executor_block
    assert "demo-sec" in executor_block  # 角色适用 → 索引仍在
    # 二次 run 的 planner 提示词与一次 run 完全一致（无状态残留）
    assert len(mock.planner_prompts) == 2
    assert mock.planner_prompts[0] == mock.planner_prompts[1] == PLANNER_SYSTEM_PROMPT


def test_all_roles_skill_reaches_planner(tmp_path):
    """roles 留空 = 所有角色可用：planner 的 system prompt 也携带索引与正文。"""
    skills_dir = _write_skill(tmp_path, roles="", triggers="[injection]")
    mock = RecordingMockLLM()
    orch = LangGraphOrchestrator(mock, [], skills_dir=str(skills_dir))
    orch.run(task="check sql injection", project_path=str(tmp_path))
    planner_prompt = _first_system_prompt(mock)
    assert planner_prompt.startswith(PLANNER_SYSTEM_PROMPT)
    assert INDEX_HEADER in planner_prompt
    assert "## Skill: demo-sec" in planner_prompt


def test_factory_env_skils_dir_fallback(tmp_path, monkeypatch):
    _write_skill(tmp_path, roles="[executor]")
    # $SKILLS_DIR 生效
    monkeypatch.setenv("SKILLS_DIR", str(tmp_path))
    orch = create_langgraph_orchestrator(object(), [])
    assert [s.name for s in orch.skills] == ["demo-sec"]
    monkeypatch.delenv("SKILLS_DIR")
    # 显式参数优先于环境变量
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("SKILLS_DIR", str(empty))
    orch = create_langgraph_orchestrator(object(), [], skills_dir=str(tmp_path))
    assert len(orch.skills) == 1
    monkeypatch.delenv("SKILLS_DIR")


def test_factory_empty_env_string_treated_as_unset(tmp_path, monkeypatch):
    monkeypatch.setenv("SKILLS_DIR", "")
    orch = create_langgraph_orchestrator(object(), [])
    assert {s.name for s in orch.skills} == {s.name for s in load_skills()}


def test_factory_default_is_repo_root_skills(monkeypatch):
    monkeypatch.delenv("SKILLS_DIR", raising=False)
    orch = create_langgraph_orchestrator(object(), [])
    assert {s.name for s in orch.skills} == {s.name for s in load_skills()}


def test_entrypoint_static_guards():
    """CLI 暴露 --skills-dir；工厂承担 env 回退；Streamlit/API 不改调用点即继承。"""
    cli_source = Path("src/app/cli_multi.py").read_text(encoding="utf-8")
    assert "--skills-dir" in cli_source
    assert "skills_dir=args.skills_dir" in cli_source

    factory_source = Path("src/multi_agent/factory.py").read_text(encoding="utf-8")
    assert 'os.environ.get("SKILLS_DIR")' in factory_source

    for path in ("src/app/streamlit_app.py", "src/api/server.py"):
        source = Path(path).read_text(encoding="utf-8")
        assert "create_langgraph_orchestrator" in source
        assert "skills_dir=" not in source  # 技能继承只经工厂默认，改动显式可见
