from types import SimpleNamespace

import pytest

from pcoptimizer import ai_settings
from pcoptimizer.ai_assistant import TOOLS, AIChangeLog, Assistant, is_read_only
from pcoptimizer.backend import DemoAIClient, DemoBackend


class ScriptedClient:
    """Returns canned responses and records every request."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        return self.responses.pop(0)


def text(t):
    return SimpleNamespace(type="text", text=t)


def tool_use(id_, name, input_):
    return SimpleNamespace(type="tool_use", id=id_, name=name, input=input_)


def msg(stop, *blocks):
    return SimpleNamespace(stop_reason=stop, content=list(blocks))


@pytest.fixture
def backend(tmp_path):
    return DemoBackend(data_dir=tmp_path)


# ----------------------------------------------------------------------------- read-only detection

@pytest.mark.parametrize("cmd,shell", [
    ("ipconfig /all", "cmd"), ("systeminfo", "cmd"), ("powercfg /list", "cmd"), ("ping google.com -n 4", "cmd"),
    ("netsh wlan show interfaces", "cmd"), ("Get-Process | Sort-Object CPU -Descending | Select-Object -First 10",
                                            "powershell"),
    ("Get-NetAdapter", "powershell"), ("Get-PhysicalDisk | Format-Table", "powershell"),
])
def test_read_only_commands(cmd, shell):
    assert is_read_only(cmd, shell)


@pytest.mark.parametrize("cmd,shell", [
    ("ipconfig /release", "cmd"), ("ipconfig /all & del C:\\x", "cmd"), ("netsh winsock reset", "cmd"),
    ("powercfg /h off", "cmd"), ("ping x.com > out.txt", "cmd"), ("del /q %TEMP%\\*", "cmd"),
    ("Get-Process | Stop-Process", "powershell"), ("Get-ChildItem C:\\ | Remove-Item", "powershell"),
    ("Get-Date; Restart-Computer", "powershell"), ("Get-Item $(Remove-Item x)", "powershell"),
    ("Get-Content x | ForEach-Object { Remove-Item $_ }", "powershell"), ("[IO.File]::Delete('x')", "powershell"),
    ("Set-ItemProperty HKCU:\\x -Name y -Value 1", "powershell"), ("", "cmd"),
])
def test_changing_commands_need_approval(cmd, shell):
    assert not is_read_only(cmd, shell)


# ----------------------------------------------------------------------------- tool schemas

def test_tool_schemas_are_strict_and_complete():
    names = set()
    for t in TOOLS:
        schema = t["input_schema"]
        assert t["strict"] and schema["additionalProperties"] is False
        assert sorted(schema["required"]) == sorted(schema["properties"])
        names.add(t["name"])
    assert len(names) == len(TOOLS)
    # every tool has a handler
    for name in names:
        assert hasattr(Assistant, f"_t_{name}"), name


# ----------------------------------------------------------------------------- conversation loop

def test_loop_runs_tools_and_returns_final_text(backend):
    client = ScriptedClient([
        msg("tool_use", text("Checking."), tool_use("a", "get_pc_overview", {})),
        msg("end_turn", text("All good.")),
    ])
    a = Assistant(backend, client=client)
    reply = a.send("is my pc ok?")
    assert reply == "Checking.\n\nAll good."
    first = client.requests[0]
    assert first["model"] == "claude-opus-5-5"
    assert first["fallbacks"] == "default" and first["betas"] == ["server-side-fallback-2026-07-01"]
    assert first["thinking"] == {"type": "adaptive"}
    # second request carries the tool result for the first tool call
    result = client.requests[1]["messages"][-1]["content"][0]
    assert result["tool_use_id"] == "a" and not result["is_error"]
    assert '"rating"' in result["content"]
    # history: user, assistant, tool results, assistant - append-only
    assert [m["role"] for m in a.messages] == ["user", "assistant", "user", "assistant"]


def test_declined_action_is_not_performed(backend):
    client = ScriptedClient([
        msg("tool_use", tool_use("a", "apply_optimizations", {"ids": ["game_dvr_off"], "reason": "faster"})),
        msg("end_turn", text("OK, I left it.")),
    ])
    asked = []
    a = Assistant(backend, client=client, approve=lambda *args: asked.append(args) or False)
    a.send("speed up")
    assert asked and asked[0][0] == "Apply optimizations"
    assert not backend.tweaks.is_applied("game_dvr_off")
    result = client.requests[1]["messages"][-1]["content"][0]
    assert result["is_error"] and "declined" in result["content"]


def test_approved_action_runs(backend):
    client = ScriptedClient([
        msg("tool_use", tool_use("a", "apply_optimizations", {"ids": ["game_dvr_off"], "reason": "faster"})),
        msg("end_turn", text("Done.")),
    ])
    Assistant(backend, client=client, approve=lambda *_: True).send("speed up")
    assert backend.tweaks.is_applied("game_dvr_off")


def test_read_only_command_runs_without_asking(backend):
    ran = []
    backend.run_command = lambda cmd, on_line, timeout=None: (ran.append(cmd), on_line("Windows IP"), 0)[2]
    client = ScriptedClient([
        msg("tool_use", tool_use("a", "run_command", {"command": "ipconfig /all", "shell": "cmd",
                                                      "changes_system": False, "reason": "check network"})),
        msg("end_turn", text("Done.")),
    ])
    Assistant(backend, client=client, approve=lambda *_: pytest.fail("should not ask")).send("net?")
    assert ran == ["ipconfig /all"]


def test_registry_change_is_backed_up_and_undoable(backend, tmp_path):
    reg = backend.registry
    reg.set("HKCU", r"Control Panel\Mouse", "MouseSensitivity", "10", "SZ")
    client = ScriptedClient([
        msg("tool_use", tool_use("a", "set_registry_value", {
            "hive": "HKCU", "key": r"Control Panel\Mouse", "name": "MouseSensitivity", "value_type": "SZ",
            "value": "14", "reason": "Faster pointer"})),
        msg("end_turn", text("Done.")),
    ])
    Assistant(backend, client=client, approve=lambda *_: True).send("faster mouse")
    assert reg.get("HKCU", r"Control Panel\Mouse", "MouseSensitivity")[0] == "14"
    change_id = backend.ai_changes.changes[0].id
    # survives a restart: a fresh change log registers it again
    assert change_id in {c.id for c in AIChangeLog(backend.tweaks, tmp_path / "ai_changes.json").changes}
    assert backend.tweaks.revert(change_id)
    assert reg.get("HKCU", r"Control Panel\Mouse", "MouseSensitivity")[0] == "10"


def test_refusal_and_unknown_tool(backend):
    client = ScriptedClient([msg("refusal")])
    assert "can't help" in Assistant(backend, client=client).send("x")
    a = Assistant(backend, client=ScriptedClient([]))
    out, is_error = a.run_tool("nope", {})
    assert is_error


def test_settings_uri_validation(backend):
    a = Assistant(backend, client=ScriptedClient([]))
    assert a.run_tool("open_windows_settings", {"uri": "ms-settings:display"})[1] is False
    assert a.run_tool("open_windows_settings", {"uri": "ms-settings:x & del C:\\"})[1] is True


def test_demo_client_script(backend):
    a = Assistant(backend, client=DemoAIClient(), approve=lambda *_: True)
    reply = a.send("why is my pc slow?")
    assert "fixed them" in reply
    assert backend.tweaks.is_applied("startup_delay")


def test_api_key_storage(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    path = tmp_path / "ai.json"
    assert ai_settings.load_api_key(path) is None
    ai_settings.save_api_key(" sk-ant-test ", path)
    assert ai_settings.load_api_key(path) == "sk-ant-test"
    ai_settings.forget_api_key(path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env")
    assert ai_settings.load_api_key(path) == "sk-ant-env"
