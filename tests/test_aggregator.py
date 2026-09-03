"""Unit tests for the A2A response aggregator (no network required)."""

from __future__ import annotations

from a2a import types as a2a_types

from a2a_mcp.a2a_client import _ResponseAggregator


def _msg(message_id: str, text: str, role: int = a2a_types.Role.ROLE_AGENT) -> a2a_types.Message:
    return a2a_types.Message(
        message_id=message_id,
        role=role,
        parts=[a2a_types.Part(text=text)],
    )


def _status_update_event(task_id: str, state_name: str, text: str | None = None) -> a2a_types.TaskStatusUpdateEvent:
    state_enum = getattr(a2a_types.TaskState, state_name)
    status = a2a_types.TaskStatus()
    status.state = state_enum
    if text:
        status.message.CopyFrom(_msg("m-fail", text, role=a2a_types.Role.ROLE_AGENT))
    return a2a_types.TaskStatusUpdateEvent(task_id=task_id, status=status)


def _status_update(task_id: str, state_name: str, text: str | None = None) -> a2a_types.StreamResponse:
    """Wrap a status_update event into the StreamResponse the aggregator expects."""
    return _stream("status_update", status_update=_status_update_event(task_id, state_name, text))


def _artifact_update(artifact_id: str, name: str, description: str, text: str) -> a2a_types.TaskArtifactUpdateEvent:
    art = a2a_types.Artifact(
        artifact_id=artifact_id,
        name=name,
        description=description,
        parts=[a2a_types.Part(text=text)],
    )
    return a2a_types.TaskArtifactUpdateEvent(task_id="t-1", artifact=art)


def _task(task_id: str, context_id: str, history_texts: list[str], artifacts: list[a2a_types.Artifact]) -> a2a_types.Task:
    task = a2a_types.Task(id=task_id, context_id=context_id)
    task.status.state = a2a_types.TaskState.TASK_STATE_COMPLETED
    for text in history_texts:
        task.history.append(_msg("m", text))
    for art in artifacts:
        task.artifacts.append(art)
    return task


def _stream(payload_name: str, **payload_kwargs) -> a2a_types.StreamResponse:
    """Build a StreamResponse with the given payload field set."""
    resp = a2a_types.StreamResponse()
    if payload_name == "task":
        resp.task.CopyFrom(payload_kwargs["task"])
    elif payload_name == "message":
        resp.message.CopyFrom(payload_kwargs["message"])
    elif payload_name == "status_update":
        resp.status_update.CopyFrom(payload_kwargs["status_update"])
    elif payload_name == "artifact_update":
        resp.artifact_update.CopyFrom(payload_kwargs["artifact_update"])
    return resp


def test_terminal_status_ends_aggregation():
    agg = _ResponseAggregator()
    agg.feed(_status_update("t-1", "TASK_STATE_WORKING"))
    assert not agg.is_terminal
    agg.feed(_status_update("t-1", "TASK_STATE_COMPLETED"))
    assert agg.is_terminal
    assert agg.state == "TASK_STATE_COMPLETED"
    assert agg.task_id == "t-1"
    result = agg.to_result()
    assert result.state == "TASK_STATE_COMPLETED"
    assert result.task_id == "t-1"


def test_agent_message_text_is_collected():
    agg = _ResponseAggregator()
    agg.feed(_stream("message", message=_msg("m1", "Hello ")))
    agg.feed(_stream("message", message=_msg("m2", "world.")))
    agg.feed(_status_update("t-1", "TASK_STATE_COMPLETED"))
    result = agg.to_result()
    assert result.agent_response == "Hello \nworld."


def test_failed_status_carries_error_text():
    agg = _ResponseAggregator()
    agg.feed(_status_update("t-1", "TASK_STATE_FAILED", text="Rate limit exceeded"))
    result = agg.to_result()
    assert result.state == "TASK_STATE_FAILED"
    assert result.agent_response == "Rate limit exceeded"


def test_artifact_collected_with_latest_winning():
    a1 = _artifact_update("a-1", "report", "v1", "first")
    a2 = _artifact_update("a-1", "report", "v2", "second")
    agg = _ResponseAggregator()
    agg.feed(_stream("artifact_update", artifact_update=a1))
    agg.feed(_stream("artifact_update", artifact_update=a2))
    agg.feed(_status_update("t-1", "TASK_STATE_COMPLETED"))
    result = agg.to_result()
    assert len(result.artifacts) == 1
    assert result.artifacts[0].description == "v2"
    assert result.artifacts[0].parts[0].text == "second"


def test_task_history_aggregates_agent_text():
    """When the final task arrives with history, agent texts are folded in."""
    task = _task(
        task_id="t-1",
        context_id="ctx-1",
        history_texts=["From history"],
        artifacts=[],
    )
    agg = _ResponseAggregator()
    agg.feed(_stream("message", message=_msg("m-stream", "From stream")))
    agg.feed(_stream("task", task=task))
    result = agg.to_result()
    assert result.task_id == "t-1"
    assert result.context_id == "ctx-1"
    assert result.state == "TASK_STATE_COMPLETED"
    assert "From stream" in result.agent_response
    assert "From history" in result.agent_response