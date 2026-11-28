import json
import os
import socket
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import uvicorn
import kge.app as kgeapp
from kge.services.modelgateway import routes
from kge.adapters.modelusage import current_usage
from kge.ports import ChatEvent, ChatPart
from kge.eval.sets import items
from kge.estate.golden_sage import GOLDEN_20

rows = [
    x
    for x in items(os.environ["GOLDEN_SET"])
    if x.get("category") == GOLDEN_20 and not x.get("variant_of")
]
if os.environ.get("RULES_USAGE_QUESTION"):
    rows = [{"id": "rules-usage", "question": os.environ["RULES_USAGE_QUESTION"]}]
Path(os.environ["GOLDEN_QUESTIONS"]).write_text(
    json.dumps([{"id": x["id"], "question": x["question"]} for x in rows])
)


class Scripted:
    provider = "mock"
    model = "scripted-golden"
    name = "mock:scripted-golden"
    prices = {"in": 2, "out": 4}

    def stream_chat(self, request):
        ledger, question, invocation = current_usage()
        attempt = str(uuid.uuid4())
        ledger.start_attempt(question, attempt, invocation)
        ledger.finish_attempt(
            question,
            attempt,
            {
                "outcome": "ok",
                "input_tokens": 10,
                "output_tokens": 4,
                "thinking_tokens": 1,
                "cache_read_tokens": None,
                "cache_write_tokens": None,
                "input_price": 2,
                "output_price": 4,
                "cache_read_price": None,
                "cache_write_price": None,
                "cache_mode": "none",
            },
        )
        if "untrusted data and never instructions" in request.instructions:
            yield ChatEvent(part=ChatPart("text", text='{"answer":"","sources":[]}'))
        else:
            user = next(m for m in reversed(request.messages) if m.role == "user")
            question = "".join(p.text for p in user.parts if p.kind == "text")
            yield ChatEvent(
                part=ChatPart(
                    "call",
                    call_id="call-" + str(uuid.uuid4()),
                    name="ask",
                    value={"question": question},
                )
            )
        yield ChatEvent(
            model=self.model,
            tokens_in=10,
            tokens_out=4,
            tokens_thinking=1,
            finish="STOP",
        )


routes.model_for = lambda purpose, **kwargs: Scripted()
sock = socket.socket()
sock.bind(("127.0.0.1", 0))
sock.listen(128)
port = sock.getsockname()[1]
server = uvicorn.Server(uvicorn.Config(kgeapp.app, log_level="warning", lifespan="on"))
thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
thread.start()
while not server.started and thread.is_alive():
    time.sleep(0.05)
if not server.started:
    raise RuntimeError("KGE server failed to start")
try:
    p = subprocess.run(
        [
            os.environ.get("NODE_BINARY", "node"),
            "--experimental-sqlite",
            str(Path(__file__).with_suffix(".mjs")),
        ],
        env={**os.environ, "ENDPOINT_PORT": str(port), "PORTAL_KEY": "s" * 32},
        text=True,
        capture_output=True,
        timeout=900,
    )
    print(p.stdout)
    print(p.stderr, file=sys.stderr)
    assert p.returncode == 0, p.returncode
    if not os.environ.get("RULES_USAGE_QUESTION"):
        from kge.eval.run_golden import asking_runtimes, judge

        eng = kgeapp.engine()
        person = "analyst_enterprise.meridian@example.com"
        byid = {x["id"]: x for x in rows}
        cache = {}
        checked = []
        for item in json.loads(Path(os.environ["GOLDEN_RESULTS"]).read_text()):
            reference = byid[item["id"]]
            requested = asking_runtimes(reference)
            kwargs = {"runtimes": requested} if requested is not None else {}
            baseline = eng.answer(item["question"], person, **kwargs)

            def score(answer):
                return judge(
                    reference,
                    answer,
                    w=eng.w,
                    runtimes=requested,
                    readable_units=eng.g.readable_units(person),
                    cache=cache,
                    default_unit="wealth",
                )

            try:
                bv = score(baseline)
                final = next(
                    (x for x in reversed(item.get("replies", [])) if x.get("behaviour")),
                    None,
                )
                av = score(final) if final else None
                checked.append(
                    {
                        "id": item["id"],
                        "baseline_status": bv["status"],
                        "agent_status": av["status"] if av else None,
                        "baseline_behaviour": baseline.get("behaviour"),
                        "agent_behaviour": final.get("behaviour") if final else None,
                        "agent_error": item.get("error"),
                        "usage": item.get("usage"),
                        "same_rows": (
                            baseline.get("rows") == final.get("rows")
                            and baseline.get("figure") == final.get("figure")
                        )
                        if final
                        else False,
                    }
                )
            except Exception as exc:
                checked.append(
                    {"id": item["id"], "score_error": type(exc).__name__ + ": " + str(exc)}
                )
        Path(os.environ["GOLDEN_SCORES"]).write_text(
            json.dumps(checked, indent=2, default=str)
        )
finally:
    server.should_exit = True
    thread.join(20)
    sock.close()
