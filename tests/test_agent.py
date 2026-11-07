import json

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from datapilot.agent import Agent
from datapilot.tools import ToolDefinition, ToolExecutionError, ToolRegistry


def call(call_id, name, args):
    return {"role": "assistant", "content": None, "tool_calls": [
        {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
    ]}


class Model:
    def __init__(self):
        self.index = 0

    def complete(self, messages, tools):
        assert {item["function"]["name"] for item in tools} == {"query_database", "database_schema", "search_knowledge"}
        self.index += 1
        if self.index == 1:
            return call("first", "query_database", {"sql": "wrong"})
        if self.index == 2:
            assert messages[-1]["tool_call_id"] == "first"
            assert json.loads(messages[-1]["content"])["error_code"] == "unknown_column"
            return call("second", "database_schema", {})
        if self.index == 3:
            return call("third", "query_database", {"sql": "good"})
        assert messages[-1]["tool_call_id"] == "third"
        assert json.loads(messages[-1]["content"])["data"]["id"] == "Q1"
        return {"role": "assistant", "content": "4 orders [Q1]"}


class Source:
    def __init__(self):
        self.queries = []

    def execute(self, args):
        self.queries.append(args["sql"])
        if args["sql"] == "wrong":
            raise ToolExecutionError("unknown_column", "Read schema")
        return {"sql": "SELECT COUNT(*) FROM orders", "rows": [[4]], "columns": ["count"], "truncated": False}

    def tools(self):
        registry = ToolRegistry()
        registry.register(ToolDefinition("query_database", "query", {"type": "object"}, self.execute))
        registry.register(ToolDefinition("database_schema", "schema", {"type": "object"}, lambda _: {"orders": []}))
        return registry


class Knowledge:
    def search(self, workspace, source, question, key):
        return {"rule": [{"key": key, "version": 2, "text": "rule", "chunk_id": "r2"}], "matches": []}


class Store:
    def __init__(self):
        self.events = []
        self.shared = 0

    def event(self, run, key, kind, detail):
        self.events.append((key, kind, detail))

    def share(self, run):
        self.shared += 1
        return True


def test_native_repair_pause_and_resume():
    model, source, store, saver = Model(), Source(), Store(), InMemorySaver()
    graph = Agent(model, source, Knowledge(), store, saver).graph
    config = {"configurable": {"thread_id": "task"}}
    result = graph.invoke({"run_id": "task", "workspace": "demo", "source": "orders", "mode": "report",
                           "question": "退款率", "rule_key": "refund_rate", "messages": []}, config)
    assert result["__interrupt__"]
    assert result["evidence"][0]["rows"] == [[4]]
    assert store.shared == 0
    restarted = Agent(model, source, Knowledge(), store, saver).graph
    done = restarted.invoke(Command(resume=True), config)
    assert done["status"] == "shared"
    assert source.queries == ["wrong", "good"]
    assert store.shared == 1


def test_unknown_tool_stops_without_query():
    class BadModel(Model):
        def complete(self, messages, tools):
            return call("bad", "unregistered", {})
    source, store = Source(), Store()
    graph = Agent(BadModel(), source, Knowledge(), store, InMemorySaver()).graph
    result = graph.invoke({"run_id": "task", "workspace": "demo", "source": "orders", "mode": "ask",
                           "question": "show", "messages": []}, {"configurable": {"thread_id": "task"}})
    assert result["error"] == "unknown_tool"
    assert source.queries == []
