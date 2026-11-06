from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Callable
from uuid import uuid4

from fastapi import Depends, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse
import httpx
from pydantic import BaseModel, Field

from data_platform.knowledge.store import KnowledgeStore
from data_platform.runtime.models import RunStatus
from data_platform.runtime.store import RunStore

from .store import FeatureStore


class ResourceRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    config: dict = Field(default_factory=dict)
    enabled: bool = True


class ResourceUpdateRequest(BaseModel):
    expected_version: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    config: dict = Field(default_factory=dict)
    enabled: bool | None = None


class ShareRequest(BaseModel):
    resource_type: str = Field(min_length=1, max_length=40)
    resource_id: str = Field(min_length=1, max_length=120)
    ttl_seconds: int = Field(default=3600, ge=60, le=604800)


class FeedbackRequest(BaseModel):
    run_id: str = Field(min_length=1, max_length=120)
    rating: int = Field(ge=1, le=5)
    comment: str = Field(default="", max_length=4000)


class FileRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=1, max_length=2_000_000)
    content_type: str = Field(default="text/plain", max_length=120)
    encoding: str = Field(default="utf-8", pattern="^(utf-8|base64)$")


class DatasetRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    cases: list[dict] = Field(min_length=1, max_length=1000)


class EvalRunRequest(BaseModel):
    dataset_id: str = Field(min_length=1)


class ScheduleRequest(ResourceRequest):
    cron: str = Field(min_length=1, max_length=120)
    payload: dict = Field(default_factory=dict)


class GraphEntityRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    entity_type: str = Field(min_length=1, max_length=80)
    properties: dict = Field(default_factory=dict)


class GraphRelationRequest(BaseModel):
    source_id: str = Field(min_length=1)
    target_id: str = Field(min_length=1)
    relation: str = Field(min_length=1, max_length=120)
    properties: dict = Field(default_factory=dict)


class SandboxRequest(BaseModel):
    command: list[str] = Field(min_length=1, max_length=20)
    timeout_seconds: int = Field(default=5, ge=1, le=30)


RESOURCE_KINDS = {
    "models", "connectors", "agents", "skills", "applications", "prompts",
    "plugins",
}


def register_feature_routes(
    app,
    *,
    features: FeatureStore,
    knowledge: KnowledgeStore,
    runs: RunStore,
    data_root: Path,
    current_user: Callable,
    require_member: Callable,
    identity,
) -> None:
    """Register the platform surfaces that are independent of the core chat loop."""

    def owner(workspace_id: str, user_id: str) -> None:
        require_member(workspace_id, user_id)
        if identity.role(user_id, workspace_id) != "owner":
            raise HTTPException(403, "Owner role required")

    def resource_list(kind: str, workspace_id: str, user_id: str):
        if kind not in RESOURCE_KINDS:
            raise HTTPException(404, "Resource type not found")
        require_member(workspace_id, user_id)
        return {kind: features.list(workspace_id, kind[:-1] if kind.endswith("s") else kind)}

    def save_resource(kind: str, workspace_id: str, request: ResourceRequest, user_id: str):
        owner(workspace_id, user_id)
        singular = kind[:-1] if kind.endswith("s") else kind
        return features.upsert(workspace_id, singular, request.name, request.config,
                               enabled=request.enabled)

    for kind in RESOURCE_KINDS:
        singular = kind[:-1] if kind.endswith("s") else kind

        def list_handler(workspace_id: str, user_id: str = Depends(current_user), _kind=kind):
            return resource_list(_kind, workspace_id, user_id)

        def create_handler(workspace_id: str, request: ResourceRequest,
                          user_id: str = Depends(current_user), _kind=kind):
            return save_resource(_kind, workspace_id, request, user_id)

        def get_handler(workspace_id: str, resource_id: str,
                        user_id: str = Depends(current_user), _kind=singular):
            require_member(workspace_id, user_id)
            item = features.get(workspace_id, _kind, resource_id)
            if item is None:
                raise HTTPException(404, "Resource not found")
            return item

        def update_handler(workspace_id: str, resource_id: str, request: ResourceUpdateRequest,
                           user_id: str = Depends(current_user), _kind=singular):
            owner(workspace_id, user_id)
            try:
                return features.update(workspace_id, _kind, resource_id, request.config,
                                       expected_version=request.expected_version,
                                       name=request.name, enabled=request.enabled)
            except LookupError as exc:
                raise HTTPException(404, "Resource not found") from exc
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from exc

        def delete_handler(workspace_id: str, resource_id: str,
                          user_id: str = Depends(current_user), _kind=singular):
            owner(workspace_id, user_id)
            if not features.delete(workspace_id, _kind, resource_id):
                raise HTTPException(404, "Resource not found")
            return {"status": "deleted"}

        app.add_api_route(f"/v1/workspaces/{{workspace_id}}/{kind}", list_handler,
                          methods=["GET"], name=f"list_{kind}")
        app.add_api_route(f"/v1/workspaces/{{workspace_id}}/{kind}", create_handler,
                          methods=["POST"], status_code=201, name=f"create_{kind}")
        app.add_api_route(f"/v1/workspaces/{{workspace_id}}/{kind}/{{resource_id}}",
                          get_handler, methods=["GET"], name=f"get_{kind}")
        app.add_api_route(f"/v1/workspaces/{{workspace_id}}/{kind}/{{resource_id}}",
                          update_handler, methods=["PUT"], name=f"update_{kind}")
        app.add_api_route(f"/v1/workspaces/{{workspace_id}}/{kind}/{{resource_id}}",
                          delete_handler, methods=["DELETE"], name=f"delete_{kind}")

    @app.get("/v1/workspaces/{workspace_id}/schedules")
    def list_schedules(workspace_id: str, user_id: str = Depends(current_user)):
        require_member(workspace_id, user_id)
        return {"schedules": features.list(workspace_id, "schedule")}

    @app.post("/v1/workspaces/{workspace_id}/schedules", status_code=201)
    def create_schedule(workspace_id: str, request: ScheduleRequest,
                        user_id: str = Depends(current_user)):
        owner(workspace_id, user_id)
        payload = {**request.config, "cron": request.cron, "payload": request.payload}
        return features.upsert(workspace_id, "schedule", request.name, payload,
                               enabled=request.enabled)

    @app.get("/v1/workspaces/{workspace_id}/schedules/{resource_id}")
    def get_schedule(workspace_id: str, resource_id: str,
                     user_id: str = Depends(current_user)):
        require_member(workspace_id, user_id)
        item = features.get(workspace_id, "schedule", resource_id)
        if item is None:
            raise HTTPException(404, "Schedule not found")
        return item

    @app.put("/v1/workspaces/{workspace_id}/schedules/{resource_id}")
    def update_schedule(workspace_id: str, resource_id: str, request: ScheduleRequest,
                        expected_version: int = 1,
                        user_id: str = Depends(current_user)):
        owner(workspace_id, user_id)
        payload = {**request.config, "cron": request.cron, "payload": request.payload}
        try:
            return features.update(workspace_id, "schedule", resource_id, payload,
                                   expected_version=expected_version,
                                   name=request.name, enabled=request.enabled)
        except LookupError as exc:
            raise HTTPException(404, "Schedule not found") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.delete("/v1/workspaces/{workspace_id}/schedules/{resource_id}")
    def delete_schedule(workspace_id: str, resource_id: str,
                        user_id: str = Depends(current_user)):
        owner(workspace_id, user_id)
        if not features.delete(workspace_id, "schedule", resource_id):
            raise HTTPException(404, "Schedule not found")
        return {"status": "deleted"}

    @app.get("/v1/workspaces/{workspace_id}/models/{resource_id}/health")
    def model_health(workspace_id: str, resource_id: str, user_id: str = Depends(current_user)):
        require_member(workspace_id, user_id)
        item = features.get(workspace_id, "model", resource_id)
        if item is None:
            raise HTTPException(404, "Model not found")
        provider = str(item["payload"].get("provider", "unknown")).lower()
        if provider == "ollama":
            # The local OpenAI-compatible endpoint does not require a secret.
            configured = True
        else:
            configured = False
        return {"id": resource_id, "provider": provider,
                "status": "ready" if configured else "unsupported_provider"}

    @app.post("/v1/workspaces/{workspace_id}/connectors/{resource_id}/test")
    def connector_health(workspace_id: str, resource_id: str,
                         user_id: str = Depends(current_user)):
        require_member(workspace_id, user_id)
        item = features.get(workspace_id, "connector", resource_id)
        if item is None:
            raise HTTPException(404, "Connector not found")
        endpoint = item["payload"].get("endpoint")
        if not endpoint:
            return {"status": "configured", "detail": "No remote endpoint configured"}
        try:
            response = httpx.get(str(endpoint), timeout=3.0, follow_redirects=False)
            return {"status": "ok" if response.status_code < 500 else "error",
                    "http_status": response.status_code}
        except httpx.HTTPError as exc:
            return {"status": "error", "detail": type(exc).__name__}

    @app.get("/v1/workspaces/{workspace_id}/connectors/{resource_id}/tools")
    def connector_tools(workspace_id: str, resource_id: str,
                        user_id: str = Depends(current_user)):
        """Return an MCP-like tool manifest without executing remote tools."""
        require_member(workspace_id, user_id)
        item = features.get(workspace_id, "connector", resource_id)
        if item is None:
            raise HTTPException(404, "Connector not found")
        payload = item["payload"]
        endpoint = payload.get("tools_endpoint") or payload.get("endpoint")
        if not endpoint:
            return {"tools": payload.get("tools", []), "source": "configuration"}
        try:
            response = httpx.get(str(endpoint).rstrip("/") + "/tools", timeout=3.0,
                                 follow_redirects=False)
            response.raise_for_status()
            data = response.json()
            tools = data.get("tools", data) if isinstance(data, dict) else data
            if not isinstance(tools, list):
                raise ValueError("remote tool manifest must be a list")
            return {"tools": tools, "source": "remote"}
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise HTTPException(502, "Connector tool manifest unavailable") from exc

    @app.get("/v1/workspaces/{workspace_id}/model-providers")
    def model_providers(workspace_id: str, user_id: str = Depends(current_user)):
        require_member(workspace_id, user_id)
        return {"providers": [
            {"id": "ollama", "name": "Ollama（Qwen 本地）", "model": os.getenv("OLLAMA_MODEL", "qwen2.5:14b"),
             "supports_tool_calling": True, "supports_streaming": True, "configured": True},
        ]}

    @app.get("/v1/workspaces/{workspace_id}/prompts/{resource_id}/render")
    def render_prompt(workspace_id: str, resource_id: str, variables: str = "{}",
                      user_id: str = Depends(current_user)):
        require_member(workspace_id, user_id)
        item = features.get(workspace_id, "prompt", resource_id)
        if item is None:
            raise HTTPException(404, "Prompt not found")
        try:
            values = json.loads(variables)
            if not isinstance(values, dict):
                raise ValueError
            template = str(item["payload"].get("template", ""))
            return {"text": template.format_map({key: str(value) for key, value in values.items()}),
                    "version": item["version"]}
        except (ValueError, KeyError) as exc:
            raise HTTPException(400, "Prompt variables are invalid or incomplete") from exc

    @app.get("/v1/workspaces/{workspace_id}/skills/{resource_id}/content")
    def skill_content(workspace_id: str, resource_id: str,
                      user_id: str = Depends(current_user)):
        require_member(workspace_id, user_id)
        item = features.get(workspace_id, "skill", resource_id)
        if item is None:
            raise HTTPException(404, "Skill not found")
        return {"id": resource_id, "name": item["name"],
                "instructions": item["payload"].get("instructions", ""),
                "tools": item["payload"].get("tools", [])}

    @app.post("/v1/workspaces/{workspace_id}/shares", status_code=201)
    def create_share(workspace_id: str, request: ShareRequest,
                    user_id: str = Depends(current_user)):
        owner(workspace_id, user_id)
        resource_kind = request.resource_type.rstrip("s")
        if request.resource_type == "run":
            exists = runs.get_run(request.resource_id, workspace_id) is not None
        elif request.resource_type == "report":
            exists = features.get(workspace_id, "report", request.resource_id) is not None
        else:
            exists = features.get(workspace_id, resource_kind, request.resource_id) is not None
        if not exists:
            raise HTTPException(404, "Share resource not found")
        share = features.create_share(workspace_id, user_id, request.resource_type,
                                      request.resource_id, int(time.time()) + request.ttl_seconds)
        return {"token": share["token"], "expires_at": share["expires_at"],
                "url": f"/v1/shares/{share['token']}"}

    @app.get("/v1/workspaces/{workspace_id}/shares")
    def list_shares(workspace_id: str, user_id: str = Depends(current_user)):
        require_member(workspace_id, user_id)
        return {"shares": features.list_shares(workspace_id)}

    @app.delete("/v1/workspaces/{workspace_id}/shares/{token}")
    def revoke_share(workspace_id: str, token: str, user_id: str = Depends(current_user)):
        owner(workspace_id, user_id)
        if not features.revoke_share(workspace_id, token):
            raise HTTPException(404, "Share not found")
        return {"status": "revoked"}

    @app.get("/v1/shares/{token}")
    def open_share(token: str):
        share = features.share(token)
        if share is None or share["revoked"] or share["expires_at"] <= int(time.time()):
            raise HTTPException(404, "Share not found or expired")
        return {"resource_type": share["resource_type"], "resource_id": share["resource_id"],
                "workspace_id": share["workspace_id"], "expires_at": share["expires_at"]}

    @app.post("/v1/workspaces/{workspace_id}/feedback", status_code=201)
    def add_feedback(workspace_id: str, request: FeedbackRequest,
                     user_id: str = Depends(current_user)):
        require_member(workspace_id, user_id)
        if runs.get_run(request.run_id, workspace_id) is None:
            raise HTTPException(404, "Run not found")
        try:
            return features.add_feedback(workspace_id, request.run_id, user_id,
                                         request.rating, request.comment)
        except Exception as exc:
            raise HTTPException(409, "Feedback already exists for this run") from exc

    @app.get("/v1/workspaces/{workspace_id}/feedback")
    def list_feedback(workspace_id: str, run_id: str | None = None,
                      user_id: str = Depends(current_user)):
        require_member(workspace_id, user_id)
        return {"feedback": features.list_feedback(workspace_id, run_id)}

    @app.post("/v1/workspaces/{workspace_id}/files", status_code=201)
    def upload_file(workspace_id: str, request: FileRequest,
                    user_id: str = Depends(current_user)):
        require_member(workspace_id, user_id)
        if Path(request.filename).name != request.filename:
            raise HTTPException(400, "Filename must not contain a path")
        try:
            raw = base64.b64decode(request.content, validate=True) if request.encoding == "base64" else request.content.encode("utf-8")
        except (ValueError, UnicodeEncodeError) as exc:
            raise HTTPException(400, "Invalid file content") from exc
        if not raw or len(raw) > 10_000_000:
            raise HTTPException(413, "File must be between 1 byte and 10 MB")
        file_id = str(uuid4())
        relative = Path(".files") / workspace_id / f"{file_id}-{request.filename}"
        path = (data_root / relative).resolve()
        if not path.is_relative_to(data_root):
            raise HTTPException(400, "Invalid file path")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        return features.add_file(workspace_id, user_id, request.filename, str(relative),
                                 request.content_type, len(raw), hashlib.sha256(raw).hexdigest())

    @app.get("/v1/workspaces/{workspace_id}/files")
    def list_files(workspace_id: str, user_id: str = Depends(current_user)):
        require_member(workspace_id, user_id)
        return {"files": features.list_files(workspace_id)}

    @app.get("/v1/workspaces/{workspace_id}/files/{file_id}")
    def download_file(workspace_id: str, file_id: str, user_id: str = Depends(current_user)):
        require_member(workspace_id, user_id)
        item = features.file(workspace_id, file_id)
        if item is None:
            raise HTTPException(404, "File not found")
        path = (data_root / item["relative_path"]).resolve()
        if not path.is_relative_to(data_root) or not path.is_file():
            raise HTTPException(404, "File content unavailable")
        return FileResponse(path, media_type=item["content_type"], filename=item["filename"])

    @app.get("/v1/workspaces/{workspace_id}/files/{file_id}/preview")
    def preview_file(workspace_id: str, file_id: str, user_id: str = Depends(current_user)):
        require_member(workspace_id, user_id)
        item = features.file(workspace_id, file_id)
        if item is None:
            raise HTTPException(404, "File not found")
        path = (data_root / item["relative_path"]).resolve()
        if not path.is_relative_to(data_root) or not path.is_file():
            raise HTTPException(404, "File content unavailable")
        if item["size"] > 2_000_000:
            raise HTTPException(413, "File is too large to preview")
        if not item["content_type"].startswith("text/") and Path(item["filename"]).suffix.lower() not in {".json", ".md", ".txt", ".csv"}:
            raise HTTPException(415, "Only text files can be previewed")
        return PlainTextResponse(path.read_text(encoding="utf-8", errors="replace"),
                                 media_type="text/plain")

    @app.delete("/v1/workspaces/{workspace_id}/files/{file_id}")
    def delete_file(workspace_id: str, file_id: str, user_id: str = Depends(current_user)):
        owner(workspace_id, user_id)
        item = features.delete_file(workspace_id, file_id)
        if item is None:
            raise HTTPException(404, "File not found")
        path = (data_root / item["relative_path"]).resolve()
        if path.is_relative_to(data_root) and path.is_file():
            path.unlink()
        return {"status": "deleted"}

    @app.post("/v1/workspaces/{workspace_id}/evaluations/datasets", status_code=201)
    def create_dataset(workspace_id: str, request: DatasetRequest,
                       user_id: str = Depends(current_user)):
        owner(workspace_id, user_id)
        return features.upsert(workspace_id, "dataset", request.name, {"cases": request.cases})

    @app.get("/v1/workspaces/{workspace_id}/evaluations/datasets")
    def list_datasets(workspace_id: str, user_id: str = Depends(current_user)):
        require_member(workspace_id, user_id)
        return {"datasets": features.list(workspace_id, "dataset")}

    @app.post("/v1/workspaces/{workspace_id}/evaluations/runs", status_code=201)
    def run_dataset(workspace_id: str, request: EvalRunRequest,
                    user_id: str = Depends(current_user)):
        require_member(workspace_id, user_id)
        dataset = features.get(workspace_id, "dataset", request.dataset_id)
        if dataset is None:
            raise HTTPException(404, "Evaluation dataset not found")
        cases = dataset["payload"].get("cases", [])
        results = []
        for index, case in enumerate(cases):
            if not isinstance(case, dict):
                results.append({"index": index, "status": "invalid"})
                continue
            sql_scored = "expected_sql" in case and "actual_sql" in case
            answer_scored = "expected_answer" in case and "actual_answer" in case
            exact_scored = "expected" in case and "actual" in case
            checks = []
            if sql_scored:
                checks.append(case["actual_sql"] == case["expected_sql"])
            if answer_scored:
                checks.append(case["actual_answer"] == case["expected_answer"])
            if exact_scored:
                checks.append(case["actual"] == case["expected"])
            status = "correct" if checks and all(checks) else "incorrect" if checks else "unscored"
            results.append({"index": index, "status": status,
                            "sql_correct": case.get("actual_sql") == case.get("expected_sql") if sql_scored else None,
                            "answer_correct": case.get("actual_answer") == case.get("expected_answer") if answer_scored else None,
                            "recovered": bool(case.get("recovered")) if case.get("fault_triggered") else None,
                            "resumed": bool(case.get("resumed")) if case.get("interrupted") else None})
        scored = [item for item in results if item["status"] in {"correct", "incorrect"}]
        correct = [item for item in results if item["status"] == "correct"]
        sql_items = [item for item in results if item["sql_correct"] is not None]
        answer_items = [item for item in results if item["answer_correct"] is not None]
        fault_items = [item for item in results if item["recovered"] is not None]
        resume_items = [item for item in results if item["resumed"] is not None]
        summary = {
            "total": len(cases), "scored": len(scored), "correct": len(correct),
            "accuracy": len(correct) / len(scored) if scored else 0.0,
            "sql_correct": sum(bool(item["sql_correct"]) for item in sql_items),
            "sql_total": len(sql_items),
            "answer_correct": sum(bool(item["answer_correct"]) for item in answer_items),
            "answer_total": len(answer_items),
            "fault_recovered": sum(bool(item["recovered"]) for item in fault_items),
            "fault_total": len(fault_items),
            "resumed": sum(bool(item["resumed"]) for item in resume_items),
            "resume_total": len(resume_items),
            "mode": "offline_structured_evaluation",
        }
        return features.upsert(workspace_id, "evaluation_run", str(uuid4()),
                               {"dataset_id": request.dataset_id, "summary": summary,
                                "results": results, "status": "succeeded"})

    @app.get("/v1/workspaces/{workspace_id}/evaluations/runs")
    def list_evaluation_runs(workspace_id: str, user_id: str = Depends(current_user)):
        require_member(workspace_id, user_id)
        return {"runs": features.list(workspace_id, "evaluation_run")}

    @app.post("/v1/workspaces/{workspace_id}/schedules/{resource_id}/trigger", status_code=201)
    def trigger_schedule(workspace_id: str, resource_id: str,
                         user_id: str = Depends(current_user)):
        owner(workspace_id, user_id)
        schedule = features.get(workspace_id, "schedule", resource_id)
        if schedule is None:
            raise HTTPException(404, "Schedule not found")
        run = features.upsert(workspace_id, "schedule_run", str(uuid4()), {
            "schedule_id": resource_id, "status": "queued",
            "payload": schedule["payload"].get("payload", {}), "triggered_by": user_id,
        })
        return run

    @app.get("/v1/workspaces/{workspace_id}/schedules/{resource_id}/runs")
    def list_schedule_runs(workspace_id: str, resource_id: str,
                           user_id: str = Depends(current_user)):
        require_member(workspace_id, user_id)
        return {"runs": features.list(workspace_id, "schedule_run")}

    @app.get("/v1/workspaces/{workspace_id}/reports")
    def list_reports(workspace_id: str, user_id: str = Depends(current_user)):
        require_member(workspace_id, user_id)
        return {"reports": features.list(workspace_id, "report")}

    @app.post("/v1/workspaces/{workspace_id}/reports", status_code=201)
    def create_report(workspace_id: str, run_id: str, title: str = "分析报告",
                      user_id: str = Depends(current_user)):
        require_member(workspace_id, user_id)
        run = runs.get_run(run_id, workspace_id)
        checkpoint = runs.load_checkpoint(run_id, workspace_id) if run else None
        if run is None or checkpoint is None or run.status != RunStatus.SUCCEEDED:
            raise HTTPException(404, "Run not found")
        report = features.upsert(workspace_id, "report", f"{run_id}:{title}",
                                 {"run_id": run_id, "title": title,
                                  "answer": checkpoint[1].get("answer"),
                                  "state": checkpoint[1]})
        return report

    @app.post("/v1/workspaces/{workspace_id}/knowledge-bases/{kb_id}/graph/entities", status_code=201)
    def create_entity(workspace_id: str, kb_id: str, request: GraphEntityRequest,
                      user_id: str = Depends(current_user)):
        owner(workspace_id, user_id)
        try:
            knowledge.ensure_scope(workspace_id, kb_id)
        except LookupError as exc:
            raise HTTPException(404, "Knowledge base not found") from exc
        return features.add_entity(workspace_id, kb_id, request.name, request.entity_type, request.properties)

    @app.post("/v1/workspaces/{workspace_id}/knowledge-bases/{kb_id}/graph/relations", status_code=201)
    def create_relation(workspace_id: str, kb_id: str, request: GraphRelationRequest,
                        user_id: str = Depends(current_user)):
        owner(workspace_id, user_id)
        try:
            knowledge.ensure_scope(workspace_id, kb_id)
        except LookupError as exc:
            raise HTTPException(404, "Knowledge base not found") from exc
        return features.add_relation(workspace_id, kb_id, request.source_id, request.target_id,
                                     request.relation, request.properties)

    @app.get("/v1/workspaces/{workspace_id}/knowledge-bases/{kb_id}/graph")
    def get_graph(workspace_id: str, kb_id: str, user_id: str = Depends(current_user)):
        require_member(workspace_id, user_id)
        try:
            knowledge.ensure_scope(workspace_id, kb_id)
        except LookupError as exc:
            raise HTTPException(404, "Knowledge base not found") from exc
        return features.list_graph(workspace_id, kb_id)

    @app.get("/v1/workspaces/{workspace_id}/knowledge-bases/{kb_id}/chunks")
    def list_chunks(workspace_id: str, kb_id: str, document_id: str | None = None,
                    user_id: str = Depends(current_user)):
        require_member(workspace_id, user_id)
        try:
            return {"chunks": knowledge.list_chunks(workspace_id, kb_id, document_id)}
        except LookupError as exc:
            raise HTTPException(404, "Knowledge base not found") from exc

    @app.post("/v1/workspaces/{workspace_id}/sandboxes/run", status_code=201)
    def run_sandbox(workspace_id: str, request: SandboxRequest,
                    user_id: str = Depends(current_user)):
        owner(workspace_id, user_id)
        if os.getenv("DATA_PLATFORM_ENABLE_SANDBOX") != "1":
            raise HTTPException(403, "Sandbox is disabled in the development build")
        if any(not isinstance(arg, str) or len(arg) > 200 for arg in request.command):
            raise HTTPException(400, "Invalid command arguments")
        try:
            completed = subprocess.run(request.command, cwd=data_root, shell=False,
                                       capture_output=True, text=True, timeout=request.timeout_seconds,
                                       check=False)
            return {"status": "succeeded" if completed.returncode == 0 else "failed",
                    "returncode": completed.returncode,
                    "stdout": completed.stdout[-10000:], "stderr": completed.stderr[-10000:]}
        except subprocess.TimeoutExpired as exc:
            return {"status": "timeout", "stdout": str(exc.stdout or "")[-10000:], "stderr": str(exc.stderr or "")[-10000:]}
