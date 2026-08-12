"""Shared LLM + tool-loop helpers for corpus agents.

Designated baseline model id: ``vernier-baseline-local-v1`` (local stub).

Live-diff vs full-stub boundary (do not invert):
  - ``VERNIER_REPLAY=1`` (full stub): LLM is served from the record.
  - ``VERNIER_DIFF=1`` (live diff): LLM must be reachable via
    ``VERNIER_LLM_BASE_URL`` / ``OPENAI_BASE_URL`` or the local stub.
  - Capture (neither set): local stub, or external URL when configured.

Agents MUST drive tools from ``message.tool_calls`` only — never a hardcoded
tool sequence. See ``docs/CORPUS-AUDIT.md``.
"""

from __future__ import annotations

import inspect
import json
import os
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, get_args, get_origin, get_type_hints

BASELINE_MODEL = "vernier-baseline-local-v1"
DEFAULT_STEP_CAP = 16
# Corpus contract: agents expose >=4 tools; models must exercise them.
TOOL_COVERAGE_HINT = (
    "You MUST call every tool in the tools list at least once before you finish. "
    "Do not stop early."
)

# Optional control: omit one tool name from the schema sent to the model (C4).
_ENV_DROP_TOOL = "VERNIER_CONTROL_DROP_TOOL"


def http_timeout() -> float:
    """Per-request timeout for slow local CPU inference (seconds)."""
    raw = (os.environ.get("VERNIER_LLM_TIMEOUT_S") or "").strip()
    if raw:
        return float(raw)
    return 600.0


def _external_llm_base() -> str | None:
    for key in ("VERNIER_LLM_BASE_URL", "OPENAI_BASE_URL"):
        raw = (os.environ.get(key) or "").strip()
        if raw:
            return raw.rstrip("/")
    return None


def _fingerprint(text: str) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) > 80:
        cleaned = cleaned[:80]
    return cleaned or "empty"


def _json_type(annotation: Any) -> dict[str, Any]:
    origin = get_origin(annotation)
    if annotation is inspect.Parameter.empty or annotation is Any:
        return {"type": "string"}
    if annotation is str:
        return {"type": "string"}
    if annotation is int:
        return {"type": "integer"}
    if annotation is float:
        return {"type": "number"}
    if annotation is bool:
        return {"type": "boolean"}
    if origin is list:
        args = get_args(annotation)
        item = _json_type(args[0]) if args else {"type": "string"}
        return {"type": "array", "items": item}
    if origin is dict:
        return {"type": "object"}
    # typing.Dict / List aliases already handled via get_origin
    name = getattr(annotation, "__name__", "")
    if name in {"str", "int", "float", "bool"}:
        return {"type": {"str": "string", "int": "integer", "float": "number", "bool": "boolean"}[name]}
    return {"type": "string"}


def tool_schema(fn: Callable[..., Any]) -> dict[str, Any]:
    """OpenAI-compatible function tool schema from a callable."""
    hints = {}
    try:
        hints = get_type_hints(fn)
    except Exception:  # noqa: BLE001
        hints = getattr(fn, "__annotations__", {}) or {}
    params = inspect.signature(fn).parameters
    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, param in params.items():
        if name in {"self", "cls"}:
            continue
        ann = hints.get(name, param.annotation)
        properties[name] = _json_type(ann)
        if param.default is inspect.Parameter.empty:
            required.append(name)
    return {
        "type": "function",
        "function": {
            "name": fn.__name__,
            "description": (fn.__doc__ or f"Call {fn.__name__}").strip().split("\n")[0],
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


def filter_tools(
    tools: dict[str, Callable[..., Any]],
) -> dict[str, Callable[..., Any]]:
    drop = (os.environ.get(_ENV_DROP_TOOL) or "").strip()
    if not drop:
        return dict(tools)
    return {k: v for k, v in tools.items() if k != drop}


def _sampling_fields() -> dict[str, Any]:
    """Stable defaults so capture and full-stub redrive share request bodies."""
    temp = (os.environ.get("VERNIER_LLM_TEMPERATURE") or "0").strip()
    seed = (os.environ.get("VERNIER_LLM_SEED") or "42").strip()
    stream = (os.environ.get("VERNIER_LLM_STREAM") or "false").strip().lower()
    out: dict[str, Any] = {
        "temperature": float(temp),
        "seed": int(seed),
    }
    if stream in {"0", "false", "no"}:
        out["stream"] = False
    elif stream in {"1", "true", "yes"}:
        out["stream"] = True
    else:
        out["stream"] = False
    num_ctx = (os.environ.get("VERNIER_LLM_NUM_CTX") or "").strip()
    if num_ctx:
        out["options"] = {"num_ctx": int(num_ctx)}
    return out


def _auth_headers() -> dict[str, str]:
    headers: dict[str, str] = {}
    api_key = (
        os.environ.get("VERNIER_LLM_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or ""
    ).strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def chat_completions(
    client: Any,
    base: str,
    *,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """POST `/v1/chat/completions`; return the parsed JSON body."""
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
    }
    payload.update(_sampling_fields())
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    headers = _auth_headers()
    response = client.post(
        f"{base}/v1/chat/completions",
        json=payload,
        headers=headers or None,
    )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise RuntimeError("chat completions response is not an object")
    return data


def _parse_args(raw: Any) -> dict[str, Any]:
    if raw is None or raw == "":
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            val = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return val if isinstance(val, dict) else {}
    return {}


def run_tool_agent(
    client: Any,
    base: str,
    *,
    model: str,
    system: str,
    user: str,
    tools: dict[str, Callable[..., Any]],
    step_cap: int = DEFAULT_STEP_CAP,
) -> list[dict[str, Any]]:
    """Model-driven tool loop.

    Sends the full tool schema every turn. Executes ONLY ``tool_calls`` from
    the model response. Stops when the model returns no tool_calls or when
    ``step_cap`` LLM turns are reached.
    """
    active = filter_tools(tools)
    if not active:
        raise RuntimeError("no tools available after filter")
    schemas = [tool_schema(fn) for fn in active.values()]
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": f"{system}\n\n{TOOL_COVERAGE_HINT}"},
        {"role": "user", "content": user},
    ]
    unused = set(active)
    nudges = 0
    for _ in range(step_cap):
        data = chat_completions(
            client,
            base,
            model=model,
            messages=messages,
            tools=schemas,
        )
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            break
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if not isinstance(message, dict):
            break
        tool_calls = message.get("tool_calls") or []
        assistant_msg: dict[str, Any] = {
            "role": "assistant",
            "content": message.get("content"),
        }
        if tool_calls:
            assistant_msg["tool_calls"] = tool_calls
        messages.append(assistant_msg)
        if not tool_calls:
            if unused and nudges < len(active):
                nudges += 1
                remaining = ", ".join(sorted(unused))
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Continue the task. You have not yet called: "
                            f"{remaining}. Call at least one of them now."
                        ),
                    }
                )
                continue
            break
        if not isinstance(tool_calls, list):
            break
        for call in tool_calls:
            if not isinstance(call, dict):
                continue
            fn = call.get("function") if isinstance(call.get("function"), dict) else {}
            name = str(fn.get("name") or "")
            call_id = str(call.get("id") or name or "call")
            if name not in active:
                result: Any = {"error": f"unknown tool {name!r}"}
            else:
                unused.discard(name)
                args = _parse_args(fn.get("arguments"))
                try:
                    result = active[name](**args)
                except TypeError as exc:
                    result = {"error": f"bad arguments for {name}: {exc}"}
                except Exception as exc:  # noqa: BLE001
                    result = {"error": f"{type(exc).__name__}: {exc}"}
            # sort_keys: replay serves ToolResult blobs as JCS (sorted keys).
            # Capture must embed the same bytes in the next LLM request body
            # or full-stub verify misses on turn 2+.
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": json.dumps(
                        result,
                        sort_keys=True,
                        separators=(",", ":"),
                        default=str,
                    ),
                }
            )
    return messages


# --- Local deterministic stub (tool_calls aware) ---------------------------------


def _user_text(messages: list[Any]) -> str:
    for item in reversed(messages):
        if isinstance(item, dict) and item.get("role") == "user":
            return str(item.get("content") or "")
    return ""


def _assistant_tool_rounds(messages: list[Any]) -> int:
    n = 0
    for item in messages:
        if (
            isinstance(item, dict)
            and item.get("role") == "assistant"
            and item.get("tool_calls")
        ):
            n += 1
    return n


def _stub_args_for(
    name: str,
    schema: dict[str, Any],
    user_text: str,
) -> dict[str, Any]:
    """Deterministic args from schema + user text (stable across re-drives)."""
    tokens = [t for t in user_text.replace(":", " ").replace(",", " ").split() if t]
    seed = tokens[-1] if tokens else "x"
    topic = " ".join(tokens[-3:]) if tokens else "topic"
    params = (
        schema.get("function", {}).get("parameters", {}).get("properties", {})
        if isinstance(schema.get("function"), dict)
        else {}
    )
    if not isinstance(params, dict):
        params = {}
    args: dict[str, Any] = {}
    for key, meta in params.items():
        if not isinstance(meta, dict):
            args[key] = seed
            continue
        typ = meta.get("type")
        if typ == "integer":
            args[key] = 2
        elif typ == "number":
            args[key] = 1.0
        elif typ == "boolean":
            args[key] = True
        elif typ == "array":
            args[key] = [seed]
        elif typ == "object":
            args[key] = {"k": seed}
        else:
            # Heuristic names used across corpus tools.
            if key in {"query", "text", "utterance", "sql", "title"}:
                args[key] = topic
            elif key in {"url"}:
                args[key] = f"doc:{seed}:1"
            elif key in {"path", "target", "module"}:
                args[key] = f"{seed}.py" if "." not in seed else seed
            elif key in {"origin"}:
                args[key] = seed.split("-")[0] if "-" in seed else "SFO"
            elif key in {"dest"}:
                parts = seed.split("-")
                args[key] = parts[1] if len(parts) > 1 else "JFK"
            elif key in {"option_id", "selected"}:
                args[key] = f"{seed}-A"
            elif key in {"passenger", "customer_id"}:
                args[key] = seed
            elif key in {"ticket_id"}:
                args[key] = "t0"
            elif key in {"tier"}:
                args[key] = "gold"
            elif key in {"issue"}:
                args[key] = "billing"
            elif key in {"action"}:
                args[key] = "refund"
            elif key in {"city"}:
                args[key] = seed
            elif key in {"expr"}:
                args[key] = seed
            elif key in {"op"}:
                args[key] = "double"
            elif key in {"patch"}:
                args[key] = f"fix:{seed}"
            elif key in {"limit"}:
                args[key] = 2
            elif key in {"rows"}:
                args[key] = [{"id": 1, "v": 10}]
            elif key in {"name", "table"}:
                args[key] = f"{seed}_out"
            elif key in {"intent"}:
                args[key] = "search"
            elif key in {"result"}:
                args[key] = {"ok": True}
            elif key in {"bullets"}:
                args[key] = [f"quote:{seed}"]
            else:
                args[key] = seed
    return args


def _stub_completion(req: dict[str, Any]) -> dict[str, Any]:
    model = str(req.get("model") or BASELINE_MODEL)
    messages = req.get("messages") if isinstance(req.get("messages"), list) else []
    tools = req.get("tools") if isinstance(req.get("tools"), list) else []
    names: list[str] = []
    schemas_by_name: dict[str, dict[str, Any]] = {}
    for item in tools:
        if not isinstance(item, dict):
            continue
        fn = item.get("function")
        if not isinstance(fn, dict):
            continue
        name = str(fn.get("name") or "")
        if name:
            names.append(name)
            schemas_by_name[name] = item

    if not names:
        # Text-only fallback (legacy callers).
        last = ""
        if messages:
            last = str(messages[-1].get("content", "")) if isinstance(messages[-1], dict) else ""
        return {
            "id": "corpus-chat",
            "model": model,
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": f"[{model}] plan:{_fingerprint(last)}",
                    }
                }
            ],
        }

    rounds = _assistant_tool_rounds(messages)
    user = _user_text(messages)
    # Call each listed tool once, then stop (final message). Enough for
    # >=4 tools when agents expose >=4 tools; projected steps >> 8.
    if rounds < len(names):
        name = names[rounds]
        args = _stub_args_for(name, schemas_by_name[name], user)
        call_id = f"stub-call-{rounds}-{name}"
        return {
            "id": "corpus-chat",
            "model": model,
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": call_id,
                                "type": "function",
                                "function": {
                                    "name": name,
                                    "arguments": json.dumps(args, separators=(",", ":")),
                                },
                            }
                        ],
                    }
                }
            ],
        }

    return {
        "id": "corpus-chat",
        "model": model,
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": f"[{model}] done:{_fingerprint(user)}",
                }
            }
        ],
    }


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        return

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        try:
            req: dict[str, Any] = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            req = {}
        body = json.dumps(_stub_completion(req)).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def start_baseline_server() -> tuple[ThreadingHTTPServer | None, str]:
    """Return (server_or_none, base_url) for chat completions."""
    if os.environ.get("VERNIER_REPLAY") == "1":
        # Prefer the capture origin so full-stub HTTP keys match the record.
        replay_base = (os.environ.get("VERNIER_REPLAY_LLM_BASE") or "").strip()
        if replay_base:
            return None, replay_base.rstrip("/")
        return None, "http://replay.invalid"

    external = _external_llm_base()
    if external is not None:
        return None, external

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    host, port = server.server_address
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://{host}:{port}"


# Back-compat name used by older call sites / docs.
def chat(client: Any, base: str, content: str, *, model: str = BASELINE_MODEL) -> str:
    """Text-only chat (no tools). Prefer :func:`run_tool_agent` for corpus agents."""
    data = chat_completions(
        client,
        base,
        model=model,
        messages=[{"role": "user", "content": content}],
        tools=None,
    )
    message = data["choices"][0]["message"]
    return str(message.get("content") or "")
