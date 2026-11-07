"""One end-to-end feature check using configured providers and demo databases."""
import json
import os
import subprocess
import sys
import time

import httpx


BASE = "http://127.0.0.1:18092"
QUESTION = "按渠道计算本月已支付订单退款率。返回 channel、paid_orders、refunded_orders 和 refund_rate，退款率用小数，按渠道排序。"
RULE = (
    "退款率分母是本月支付的去重订单数，以 orders.paid_at 判断所属月份。"
    "分子是其中至少有一条符合规则的退款记录的去重订单数。"
    "使用 orders.order_id 关联 refunds.order_id，按 orders.channel 分组。"
)


class Service:
    def __enter__(self):
        self.proc = subprocess.Popen(
            [sys.executable, "-B", "-m", "uvicorn", "datapilot.api:app", "--host", "127.0.0.1", "--port", "18092"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        for _ in range(50):
            try:
                if httpx.get(BASE + "/health", timeout=1).is_success:
                    return self
            except httpx.RequestError:
                pass
            if self.proc.poll() is not None:
                break
            time.sleep(0.2)
        self.__exit__()
        raise RuntimeError("Live API failed to start")

    def __exit__(self, *_):
        self.proc.terminate()
        self.proc.wait(timeout=8)


def request(client, method, path, token=None, **kwargs):
    headers = {"Authorization": "Bearer " + token} if token else {}
    response = client.request(method, BASE + path, headers=headers, **kwargs)
    if not response.is_success:
        raise RuntimeError(f"{method} {path} returned {response.status_code}")
    return response.json()


def main():
    with httpx.Client(timeout=200) as client:
        with Service():
            def login(user, env):
                return request(client, "POST", "/auth/login", json={"username": user, "password": os.environ[env]})["token"]
            author = login("analyst", "DEMO_ANALYST_PASSWORD")
            reviewer = login("reviewer", "DEMO_REVIEWER_PASSWORD")
            request(client, "POST", "/knowledge", author, json={
                "key": "schema_orders", "title": "退款与订单表结构",
                "text": "按渠道计算退款率时，orders.order_id 关联 refunds.order_id，orders.channel 存放渠道，orders.paid_at 是支付日期。",
            })
            runs = []
            for clause, web_count in (("只计 status=succeeded 的记录。", 1),
                                      ("计 status 为 succeeded 或 pending 的记录。", 2)):
                version = request(client, "POST", "/knowledge", author, json={
                    "key": "refund_rate", "title": "退款率口径", "text": RULE + clause,
                })
                run = request(client, "POST", "/runs", author, json={
                    "mode": "report", "question": QUESTION, "rule_key": "refund_rate", "recipient": "reviewer",
                })
                trace = request(client, "GET", f"/runs/{run['id']}/events", author)
                print(json.dumps({"version": version["version"], "status": run["status"], "run_id": run["id"],
                                  "error": run["error"], "evidence": run["evidence"],
                                  "events": [e["kind"] for e in trace],
                                  "model_errors": [e["detail"] for e in trace if e["kind"] == "model_failed"]}, ensure_ascii=True), flush=True)
                retrieval = next(e["detail"] for e in trace if e["kind"] == "knowledge_retrieved")
                assert retrieval["matches"] and retrieval["keyword_ids"] and retrieval["vector_ids"]
                assert run["status"] == "waiting"
                assert run["rule"][0]["version"] == version["version"]
                checked = False
                for evidence in run["evidence"]:
                    if not {"channel","paid_orders","refunded_orders","refund_rate"} <= set(evidence["columns"]):
                        continue
                    result = {r["channel"]: r for r in (dict(zip(evidence["columns"], row)) for row in evidence["rows"])}
                    assert set(result) == {"app", "web"}
                    assert int(result["app"]["paid_orders"]) == 4 and int(result["web"]["paid_orders"]) == 4
                    assert int(result["app"]["refunded_orders"]) == 1
                    assert int(result["web"]["refunded_orders"]) == web_count
                    assert abs(float(result["app"]["refund_rate"]) - 0.25) < 0.001
                    assert abs(float(result["web"]["refund_rate"]) - web_count / 4) < 0.001
                    checked = True
                assert checked, "Expected a four-column refund-rate evidence result"
                runs.append((run, trace))
        run, before = runs[-1]
        with Service():
            path = f"/runs/{run['id']}"
            for _ in range(2):
                approved = request(client, "POST", path + "/approval", author, json={"approved": True})
                assert approved["status"] == "shared"
            after = request(client, "GET", path + "/events", author)
            count = lambda events: sum(e["kind"] == "tool_completed" for e in events)
            assert count(before) == count(after), "Completed tools were executed again"
            assert sum(e["kind"] == "report_shared" for e in after) == 1
            inbox = request(client, "GET", "/shared", reviewer)
            assert sum(item["id"] == run["id"] for item in inbox) == 1
            received = request(client, "GET", f"/shared/{run['id']}", reviewer)
            assert received["evidence"] == run["evidence"]
            print("LIVE_SMOKE_OK", run["id"], "tools_before", count(before), "tools_after", count(after))


if __name__ == "__main__":
    main()
