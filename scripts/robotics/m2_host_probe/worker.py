"""Fixed nonrobot counter, not RobotAdapter conformance or operational authority.

The construction setup frame carries fixture pins/response bytes only. Every
counter increment still requires exact current parent Unix authority. No imports
of MuJoCo, model assets, database drivers, keys or production adapter factories.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import socket
import time
from datetime import UTC, datetime
from pathlib import Path

FRAME_LIMIT = 1024 * 1024
RPC_LIMIT = FRAME_LIMIT + 65536
SCOPE = "NONROBOT_CONSTRUCTION_PROBE"


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()


def bounded_line(stream, limit):
    raw = stream.readline(limit + 2)
    if not raw or len(raw) > limit + 1 or not raw.endswith(b"\n"):
        raise ValueError("bounded complete frame required")
    return raw[:-1]


def diagnostic(kind, **values):
    raw = canonical(dict(scope=SCOPE, kind=kind, **values)) + b"\n"
    if len(raw) > 2048:
        raise ValueError("diagnostic ceiling")
    os.write(2, raw)


def admission(request, pins):
    raw = canonical(dict(version="1.0.0", request=request, pins=pins)) + b"\n"
    if len(raw) > RPC_LIMIT + 1:
        raise ValueError("authority frame ceiling")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(2)
        connection.connect("/run/accretion/authority.sock")
        connection.sendall(raw)
        connection.shutdown(socket.SHUT_WR)
        with connection.makefile("rb") as stream:
            reply = json.loads(bounded_line(stream, 4096))
            if stream.read(1):
                raise ValueError("authority reply must end after one bounded frame")
    if (
        reply["version"] != "1.0.0"
        or reply["request_digest"] != request["request_digest"]
        or type(reply["allowed"]) is not bool
    ):
        raise ValueError("authority correlation")
    if reply["allowed"]:
        if reply["code"] is not None or reply["valid_until"] is None:
            raise ValueError("authority shape")
    elif reply["code"] is None or reply["valid_until"] is not None:
        raise ValueError("denial shape")
    return reply


def error_response(request, code):
    body = dict(
        kind="response",
        wire_version="1.0.0",
        request_id=request["request_id"],
        sequence=request["sequence"],
        op=request["payload"]["op"],
        request_digest=request["request_digest"],
        scope=request["scope"],
        outcome=dict(status="ERROR", code=code),
    )
    return canonical({**body, "response_digest": hashlib.sha256(canonical(body)).hexdigest()})


def run_worker(bootstrap, input_stream, output_stream):
    del bootstrap
    config = json.loads(Path("/run/accretion/bootstrap.json").read_bytes())
    output_stream.write(canonical(dict(scope=SCOPE, ready=True, pid=os.getpid())) + b"\n")
    output_stream.flush()
    setup = json.loads(bounded_line(input_stream, FRAME_LIMIT))
    pins = setup["pins"]
    if (
        setup["scope"] != SCOPE
        or pins["episode"]["episode_id"] != config["episode_id"]
        or pins["episode"]["lease"] != config["lease"]
    ):
        raise ValueError("initial fixture pins changed")
    counter = 0
    request = json.loads(bounded_line(input_stream, FRAME_LIMIT))
    if request["request_digest"] != setup["request_digest"]:
        raise ValueError("unexpected fixed probe request")
    if config["case"] == "ownership":
        for name in ("lease", "closure"):
            wrong = copy.deepcopy(pins)
            if name == "lease":
                wrong["episode"]["lease"]["generation"] += 1
            else:
                wrong["dependencies"]["simulator_image_digest"] = "0" * 64
            reply = admission(request, wrong)
            if reply["allowed"]:
                raise ValueError("substituted pins admitted")
            diagnostic("SUBSTITUTION_DENIED", field=name, counter=counter, code=reply["code"])
    reply = admission(request, pins)
    if not reply["allowed"]:
        diagnostic("DENIED", code=reply["code"], counter=counter)
        output_stream.write(error_response(request, reply["code"]) + b"\n")
        output_stream.flush()
        return
    expires = datetime.fromisoformat(reply["valid_until"])
    diagnostic("PERMIT_RECEIVED", counter=counter, valid_until=reply["valid_until"])
    if config["case"] == "deadline":
        # Deliberately wait beyond the capped permit. The independent parent
        # deadline should kill us first; this second check prevents mutation too.
        remaining = (expires - datetime.now(UTC)).total_seconds()
        if remaining > 1:
            raise ValueError("construction deadline cap exceeds one second")
        time.sleep(max(0, remaining) + 0.2)
    if datetime.now(UTC) >= expires:
        diagnostic("PERMIT_EXPIRED", counter=counter)
        return
    if config["case"] == "ownership":
        duplicate = admission(request, pins)
        if duplicate["allowed"]:
            raise ValueError("duplicate authority call admitted")
        diagnostic("DUPLICATE_DENIED", counter=counter, code=duplicate["code"])
    if datetime.now(UTC) >= expires:
        return
    counter += 1
    diagnostic("MUTATION", counter=counter, request_digest=request["request_digest"])
    if config["case"] == "lost_ack":
        # Retain a genuine incomplete response rather than a fabricated full ACK.
        output_stream.write(b'{"kind":"response"')
        output_stream.flush()
        return
    output_stream.write(setup["response_json"].encode() + b"\n")
    output_stream.flush()
    # Fixed idle worker: no automatic heartbeat and no second mutation path.
    time.sleep(15)
