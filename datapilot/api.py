import os
from contextlib import asynccontextmanager
from typing import Literal
from uuid import uuid4

import httpx
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.types import Command
from pydantic import BaseModel, Field

from .agent import Agent
from .jina import JinaEmbeddingProvider
from .knowledge import Knowledge
from .model import DeepSeek
from .sql import BusinessSource
from .store import Store


WORKSPACE = "demo"
SOURCE = "orders"
WEB_ORIGINS = ["http://localhost:5174", "http://127.0.0.1:5174"]
auth = HTTPBearer(auto_error=False)
passwords = PasswordHasher()


class Login(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=200)


class DemoEntry(BaseModel):
    role: Literal["analyst", "reviewer"]


class RunRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1500)
    source: Literal["orders"] = SOURCE
    mode: Literal["ask", "report"] = "ask"
    recipient: str | None = Field(None, max_length=80)
    rule_key: str | None = Field(None, max_length=80, pattern=r"^[a-z0-9_-]+$")
    conversation: str | None = Field(None, max_length=40)


class Decision(BaseModel):
    approved: bool


class Document(BaseModel):
    key: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9_-]+$")
    title: str = Field(min_length=1, max_length=120)
    text: str = Field(min_length=1, max_length=6500)


class ConfiguredEmbeddings:
    def __init__(self, client):
        self.client = client

    def passages(self, texts):
        return JinaEmbeddingProvider(client=self.client).passages(texts)

    def queries(self, texts):
        return JinaEmbeddingProvider(client=self.client).queries(texts)


def graph_config(run_id):
    return {"configurable": {"thread_id": run_id}, "recursion_limit": 32}


def project_graph(store, graph, run_id):
    snapshot = graph.get_state(graph_config(run_id))
    state = snapshot.values
    if snapshot.interrupts or (not snapshot.next and state.get("status") in {"completed", "rejected"}):
        if snapshot.interrupts:
            store.report(run_id, state["answer"], state["evidence"], state["rule"], approval=True)
            store.event(run_id, "waiting", "approval_requested", {})
        elif state["status"] == "completed":
            store.report(run_id, state["answer"], state["evidence"], state["rule"], approval=False)
        else:
            store.finish(run_id, "rejected")
    elif state.get("status") == "failed":
        store.finish(run_id, "failed", state["error"])
    return snapshot


def create_app(store, source, knowledge, model, checkpointer, *, local_demo=False):
    app = FastAPI(title="DataPilot")
    app.add_middleware(CORSMiddleware, allow_origins=WEB_ORIGINS,
                       allow_methods=["GET", "POST"], allow_headers=["Authorization", "Content-Type"])
    agent = Agent(model, source, knowledge, store, checkpointer)
    graph = agent.graph

    def user(credentials: HTTPAuthorizationCredentials | None = Depends(auth)):
        name = store.authenticate(credentials.credentials) if credentials else None
        if not name:
            raise HTTPException(401, "Authentication required")
        if not store.member(WORKSPACE, name):
            raise HTTPException(403, "Workspace membership required")
        return name

    def owner(run_id, name):
        row = store.run(run_id, WORKSPACE, name)
        if not row:
            raise HTTPException(404, "Run not found")
        return row

    @app.get("/health")
    def health():
        return {"status": "ok"}

    def demo_allowed(request: Request):
        return (local_demo and request.client is not None
                and request.client.host in {"127.0.0.1", "::1"}
                and request.url.hostname in {"127.0.0.1", "localhost", "::1"}
                and request.headers.get("origin") in WEB_ORIGINS)

    @app.get("/auth/options")
    def auth_options(request: Request):
        return {"local_demo": bool(demo_allowed(request))}

    @app.post("/auth/demo")
    def demo_entry(body: DemoEntry, request: Request):
        if not demo_allowed(request) or not store.member(WORKSPACE, body.role):
            raise HTTPException(404, "Local demo entry unavailable")
        return {"token": store.issue_token(body.role), "username": body.role, "demo": True}

    @app.post("/auth/login")
    def login(request: Login):
        hashed = store.password(request.username)
        try:
            valid = bool(hashed and passwords.verify(hashed, request.password))
        except VerificationError:
            valid = False
        if not valid:
            raise HTTPException(401, "Invalid credentials")
        return {"token": store.issue_token(request.username), "username": request.username}

    @app.get("/workspace")
    def workspace(name: str = Depends(user)):
        return {"workspace": WORKSPACE, "source": SOURCE,
                "recipients": store.recipients(WORKSPACE, name),
                "can_publish": store.member(WORKSPACE, name, publisher=True)}

    @app.get("/knowledge")
    def documents(name: str = Depends(user)):
        return knowledge.documents(WORKSPACE, SOURCE)

    @app.post("/knowledge", status_code=201)
    def publish(request: Document, name: str = Depends(user)):
        if not store.member(WORKSPACE, name, publisher=True):
            raise HTTPException(403, "Publish permission required")
        try:
            return knowledge.publish(WORKSPACE, SOURCE, request.key, request.title, request.text)
        except ValueError as exc:
            raise HTTPException(422, "Invalid document or embedding response") from exc
        except httpx.HTTPError as exc:
            raise HTTPException(503, "Embedding provider unavailable") from exc

    @app.get("/knowledge/search")
    def search_knowledge(q: str = Query(min_length=1, max_length=400), name: str = Depends(user)):
        if not q.strip():
            raise HTTPException(422, "Enter a search term")
        try:
            return knowledge.search(WORKSPACE, SOURCE, q.strip())
        except (ValueError, httpx.HTTPError) as exc:
            raise HTTPException(503, "Knowledge search unavailable") from exc

    @app.get("/knowledge/{key}")
    def knowledge_document(key: str, version: int | None = Query(None, ge=1), name: str = Depends(user)):
        document = knowledge.document(WORKSPACE, SOURCE, key, version)
        if document is None:
            raise HTTPException(404, "Knowledge document not found")
        return document

    @app.get("/sources")
    def sources(name: str = Depends(user)):
        return [{"id": SOURCE, "label": "订单与退款", "engine": "PostgreSQL", "read_only": True}]

    @app.get("/sources/{source_id}/schema")
    def source_schema(source_id: str, name: str = Depends(user)):
        if source_id != SOURCE:
            raise HTTPException(404, "Data source not found")
        try:
            return source.schema()
        except Exception as exc:
            raise HTTPException(503, "Data source unavailable") from exc

    @app.get("/conversations")
    def conversations(name: str = Depends(user)):
        return store.conversations(WORKSPACE, name)

    @app.get("/conversations/{conversation}")
    def turns(conversation: str, name: str = Depends(user)):
        result = store.turns(conversation, WORKSPACE, name)
        if result is None:
            raise HTTPException(404, "Conversation not found")
        return result

    def reserve_run(request, name):
        if not request.question.strip():
            raise HTTPException(422, "Question cannot be blank")
        if request.mode == "report" and (not request.rule_key or not request.recipient or
                                          request.recipient not in store.recipients(WORKSPACE, name)):
            raise HTTPException(422, "Select a rule and an eligible recipient")
        if request.mode == "ask" and (request.rule_key or request.recipient):
            raise HTTPException(422, "A rule and recipient are only accepted for reports")
        run_id = uuid4().hex
        data = {"id": run_id, "workspace": WORKSPACE, "owner": name, "source": request.source,
                "mode": request.mode, "question": request.question, "recipient": request.recipient,
                "rule_key": request.rule_key}
        try:
            store.create_run(data, request.conversation)
        except LookupError as exc:
            raise HTTPException(404, "Conversation not found") from exc
        return run_id

    def execute_run(run_id, name):
        with store.approval_lock(run_id) as acquired:
            if not acquired:
                return
            row = owner(run_id, name)
            if row["status"] != "running":
                return
            snapshot = project_graph(store, graph, run_id)
            if owner(run_id, name)["status"] != "running":
                return
            initial = {"run_id": run_id, "workspace": WORKSPACE, "source": row["source"],
                       "question": row["question"], "mode": row["mode"], "rule_key": row["rule_key"],
                       "messages": store.history(row["conversation"], run_id)}
            try:
                graph.invoke(None if snapshot.values else initial, graph_config(run_id), durability="sync")
            except Exception:
                store.finish(run_id, "failed", "execution_unavailable")
            else:
                project_graph(store, graph, run_id)

    @app.post("/runs", status_code=201)
    def create_run(request: RunRequest, name: str = Depends(user)):
        run_id = reserve_run(request, name)
        execute_run(run_id, name)
        return owner(run_id, name)

    @app.post("/runs/start", status_code=202)
    def start_run(request: RunRequest, tasks: BackgroundTasks, name: str = Depends(user)):
        run_id = reserve_run(request, name)
        tasks.add_task(execute_run, run_id, name)
        return owner(run_id, name)

    @app.post("/runs/{run_id}/resume", status_code=202)
    def resume_run(run_id: str, tasks: BackgroundTasks, name: str = Depends(user)):
        row = owner(run_id, name)
        if row["status"] == "running":
            tasks.add_task(execute_run, run_id, name)
        return row

    @app.get("/runs")
    def runs(name: str = Depends(user)):
        return store.runs(WORKSPACE, name)

    @app.get("/runs/{run_id}")
    def run(run_id: str, name: str = Depends(user)):
        row = owner(run_id, name)
        if row["status"] == "running":
            project_graph(store, graph, run_id)
            row = owner(run_id, name)
        return row

    @app.get("/runs/{run_id}/events")
    def events(run_id: str, name: str = Depends(user)):
        owner(run_id, name)
        return store.events(run_id)

    @app.post("/runs/{run_id}/approval")
    def approve(run_id: str, request: Decision, name: str = Depends(user)):
        owner(run_id, name)
        with store.approval_lock(run_id) as acquired:
            if not acquired:
                raise HTTPException(409, "Approval in progress")
            snapshot = project_graph(store, graph, run_id)
            row = owner(run_id, name)
            decision = request.approved
            if row["decision"] is not None and row["decision"] != decision:
                raise HTTPException(409, "A different decision was already recorded")
            if row["decision"] == decision and row["status"] in {"shared", "rejected", "failed"}:
                return row
            if row["status"] != "waiting":
                raise HTTPException(409, "Run is not awaiting approval")
            if row["decision"] is None and not store.decide(run_id, decision):
                raise HTTPException(409, "Approval expired")
            try:
                command = Command(resume=decision) if snapshot.interrupts else None
                graph.invoke(command, graph_config(run_id), durability="sync")
                project_graph(store, graph, run_id)
            except Exception as exc:
                raise HTTPException(503, "Approval recorded. Retry the same decision.") from exc
            return owner(run_id, name)

    @app.get("/shared")
    def inbox(name: str = Depends(user)):
        return store.inbox(WORKSPACE, name)

    @app.get("/shared/{run_id}")
    def shared(run_id: str, name: str = Depends(user)):
        row = store.shared_report(WORKSPACE, name, run_id)
        if not row:
            raise HTTPException(404, "Shared report not found")
        return row

    return app


@asynccontextmanager
async def lifespan(app):
    from psycopg_pool import ConnectionPool

    app_dsn = os.environ["APP_DATABASE_URL"]
    store = Store(app_dsn)
    store.setup()
    with ConnectionPool(conninfo=app_dsn, min_size=1, max_size=6,
                        kwargs={"autocommit": True, "prepare_threshold": 0}) as pool:
        saver = PostgresSaver(pool)
        saver.setup()
        with httpx.Client(timeout=httpx.Timeout(35, connect=5)) as client:
            inner = create_app(store, BusinessSource(os.environ["BUSINESS_DATABASE_URL"]),
                               Knowledge(store, ConfiguredEmbeddings(client)), DeepSeek(client), saver,
                               local_demo=os.getenv("DATAPILOT_LOCAL_DEMO") == "1")
            app.mount("/", inner)
            try:
                yield
            finally:
                app.router.routes.pop()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
