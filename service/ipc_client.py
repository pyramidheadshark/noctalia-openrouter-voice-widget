from __future__ import annotations

import json
import os
import socket
import sys
import time
import uuid
from errno import ECONNREFUSED, ENOENT
from pathlib import Path
from typing import Mapping


DEFAULT_CONFIG_PATH = Path.home() / ".config" / "noctalia-openrouter-voice-widget" / "config.json"
DEFAULT_CONNECT_TIMEOUT_SECONDS = 3.0
DEFAULT_READ_CHUNK_SIZE = 65536
RETRYABLE_SOCKET_ERRNOS = {ECONNREFUSED, ENOENT}


def load_socket_path() -> str:
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR", "").strip()
    if not runtime_dir:
        raise RuntimeError("XDG_RUNTIME_DIR is required to locate the helper service socket.")

    if DEFAULT_CONFIG_PATH.exists():
        payload = json.loads(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
        raw_socket_path = payload.get(
            "socketPath",
            "${XDG_RUNTIME_DIR}/noctalia-openrouter-voice-widget.sock",
        )
    else:
        raw_socket_path = "${XDG_RUNTIME_DIR}/noctalia-openrouter-voice-widget.sock"

    return os.path.expanduser(os.path.expandvars(raw_socket_path))


def send_request(
    socket_path: str,
    request: Mapping[str, object],
    timeout_seconds: float = DEFAULT_CONNECT_TIMEOUT_SECONDS,
) -> dict[str, object]:
    deadline = time.monotonic() + timeout_seconds
    last_error: OSError | None = None

    def remaining_time_seconds() -> float:
        remaining_seconds = deadline - time.monotonic()
        if remaining_seconds <= 0:
            raise RuntimeError(f"Timed out waiting for helper response from {socket_path}")
        return remaining_seconds

    while True:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(remaining_time_seconds())
                client.connect(socket_path)
                client.sendall((json.dumps(request) + "\n").encode("utf-8"))
                response_chunks: list[bytes] = []
                while True:
                    client.settimeout(remaining_time_seconds())
                    chunk = client.recv(DEFAULT_READ_CHUNK_SIZE)
                    if not chunk:
                        break
                    response_chunks.append(chunk)
                    if b"\n" in chunk:
                        break

            if not response_chunks:
                raise RuntimeError(f"Helper service returned an empty response for {request.get('command')}")

            response_payload = b"".join(response_chunks).split(b"\n", 1)[0]
            return json.loads(response_payload.decode("utf-8"))
        except socket.timeout as exc:
            raise RuntimeError(
                f"Timed out waiting for helper response from {socket_path}"
            ) from exc
        except FileNotFoundError as exc:
            last_error = exc
        except ConnectionRefusedError as exc:
            last_error = exc
        except OSError as exc:
            if exc.errno not in RETRYABLE_SOCKET_ERRNOS:
                raise
            last_error = exc

        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"Timed out waiting for helper socket readiness at {socket_path}"
            ) from last_error

        time.sleep(0.05)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(
            "usage: python3 service/ipc_client.py <command> [json-params]",
            file=sys.stderr,
        )
        return 2

    command = argv[1]
    params = json.loads(argv[2]) if len(argv) > 2 else {}
    request: dict[str, object] = {
        "requestId": str(uuid.uuid4()),
        "command": command,
        "params": params,
    }
    socket_path = load_socket_path()

    response = send_request(socket_path, request)
    print(json.dumps(response, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
