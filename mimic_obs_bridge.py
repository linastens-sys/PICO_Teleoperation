#!/usr/bin/env python3
"""Republish TWIST2's Redis mimic reference onto the robot's DDS bus.

Dev-machine glue for the "run the pretrained TWIST2 checkpoint on the G1 via a
g1_ctrl FSM state" path (the on-robot half is `State_Twist2`, see
Physics-Based-Walker-main/deploy/robots/g1/src/State_Twist2.cpp). TWIST2's
unchanged high-level teleop (`xrobot_teleop_to_robot_w_hand.py`: Pico full-body
-> GMR IK -> 35-dim mimic) publishes that mimic to Redis under
`action_body_unitree_g1_with_hands`; this bridge reads it and publishes it as a
`MimicObs_` sample on the `rt/mimic_obs` DDS topic, which `State_Twist2`'s
`MimicObsSubscriber` consumes. So the robot never touches Redis, and TWIST2's
laptop stack is used as-is.

This is the mimic counterpart of `examples/publish_teleop_obs.py` (which bridges
mjlab's 27-float vr_keypoints for the upper-body Teleop state). Same DDS
conventions: BestEffort/KeepLast(1), CycloneDDS interface pinning + explicit
unicast peer discovery toward the robot.

`valid` tracks freshness via TWIST2's `t_action` (ms) Redis key: if the teleop
process stalls or dies, samples go out with `valid=False` and the on-robot side
falls back to holding the default pose rather than tracking a frozen reference
(legs are live -- this matters).

Prerequisites (deploy-only, same convention as publish_teleop_obs.py):
  uv pip install cyclonedds redis

Run from the mjlab repo root, alongside TWIST2's teleop + on-robot g1_ctrl:
  uv run python examples/mimic_obs_bridge.py \\
    --net <ethernet-interface> --robot-ip 192.168.123.164
"""

# No `from __future__ import annotations`: cyclonedds-python's runtime type
# introspection needs eager annotations to resolve `array[float32, 35]` (same
# reason as publish_teleop_obs.py).

import argparse
import json
import os
import time
from dataclasses import dataclass, field

import redis
from cyclonedds.core import Policy, Qos
from cyclonedds.domain import DomainParticipant
from cyclonedds.idl import IdlStruct
from cyclonedds.idl.annotations import final as idl_final
from cyclonedds.idl.types import array, float32, int64
from cyclonedds.pub import DataWriter
from cyclonedds.topic import Topic

_TOPIC = "rt/mimic_obs"
_REDIS_KEY = "action_body_unitree_g1_with_hands"
_REDIS_TS_KEY = "t_action"  # ms; written by TWIST2's teleop each publish
_MIMIC_DIM = 35


@idl_final
@dataclass
class MimicObs_(IdlStruct, typename="MimicObs_"):
  """Must exactly match Physics-Based-Walker's include/mimic_obs/MimicObs_.idl."""

  timestamp_us: int64 = 0
  action_mimic: array[float32, 35] = field(  # type: ignore[invalid-type-form]
    default_factory=lambda: [0.0] * _MIMIC_DIM
  )
  valid: bool = False


def parse_args() -> argparse.Namespace:
  parser = argparse.ArgumentParser(
    description="Redis action_body -> DDS rt/mimic_obs bridge for State_Twist2."
  )
  parser.add_argument(
    "--net", default="eno1", help="Ethernet interface connected to the G1."
  )
  parser.add_argument(
    "--robot-ip",
    default="192.168.123.164",
    help=(
      "G1 onboard computer's IP, for explicit unicast DDS peer discovery "
      "(more robust than multicast across a switch; see publish_teleop_obs.py)."
    ),
  )
  parser.add_argument("--redis-host", default="localhost")
  parser.add_argument("--redis-port", type=int, default=6379)
  parser.add_argument("--rate", type=float, default=100.0, help="Publish rate in Hz.")
  parser.add_argument(
    "--stale-ms",
    type=float,
    default=200.0,
    help=(
      "If the teleop's t_action timestamp is older than this, publish "
      "valid=False so the robot falls back to the default pose."
    ),
  )
  return parser.parse_args()


def main() -> None:
  args = parse_args()

  # Pin CycloneDDS discovery to the G1-facing interface and add explicit
  # unicast peer discovery toward the robot -- identical rationale to
  # publish_teleop_obs.py (multicast can silently fail to traverse the link).
  # Must be set before DomainParticipant() is constructed.
  os.environ["CYCLONEDDS_URI"] = (
    f"<CycloneDDS><Domain><General><Interfaces>"
    f'<NetworkInterface name="{args.net}"/>'
    f"</Interfaces></General>"
    f'<Discovery><Peers><Peer address="{args.robot_ip}"/></Peers></Discovery>'
    f"</Domain></CycloneDDS>"
  )

  rds = redis.Redis(host=args.redis_host, port=args.redis_port, db=0)

  participant = DomainParticipant(0)
  topic = Topic(participant, _TOPIC, MimicObs_)
  qos = Qos(Policy.Reliability.BestEffort, Policy.History.KeepLast(1))
  writer = DataWriter(participant, topic, qos=qos)

  print(f"Bridging Redis '{_REDIS_KEY}' -> DDS '{_TOPIC}' at {args.rate:.0f} Hz.")
  print("  valid=False while the teleop is stale/absent (robot holds default).")

  period = 1.0 / args.rate
  warned_no_key = False
  n_published = 0
  n_valid = 0
  last_report = time.time()
  while True:
    t_start = time.time()

    mimic = [0.0] * _MIMIC_DIM
    valid = False
    raw = rds.get(_REDIS_KEY)
    if raw is not None:
      values = json.loads(raw)
      if len(values) == _MIMIC_DIM:
        mimic = [float(v) for v in values]
        # Freshness gate: the teleop stamps t_action (ms) on every publish.
        ts_raw = rds.get(_REDIS_TS_KEY)
        if ts_raw is not None:
          age_ms = time.time() * 1000.0 - float(ts_raw)
          valid = age_ms <= args.stale_ms
        else:
          valid = True  # no timestamp key -> can't gate; trust the value
        warned_no_key = False
      else:
        if not warned_no_key:
          print(
            f"[warn] '{_REDIS_KEY}' has {len(values)} values, expected {_MIMIC_DIM}"
          )
          warned_no_key = True
    elif not warned_no_key:
      print(f"[warn] '{_REDIS_KEY}' not in Redis yet -- is TWIST2's teleop running?")
      warned_no_key = True

    # Validity is signaled to the robot via timestamp_us (0 = invalid/stale),
    # NOT the boolean field: the IDL `boolean valid` does not survive the
    # cyclonedds-python -> CycloneDDS-C network CDR round-trip (it arrives 0 on
    # the robot even when set true), whereas the int64/float fields cross fine.
    # `valid` is still set for local diagnostics, but the robot ignores it.
    writer.write(
      MimicObs_(
        timestamp_us=int(time.time() * 1e6) if valid else 0,
        action_mimic=mimic,  # pyright: ignore[reportArgumentType]
        valid=valid,
      )
    )
    n_published += 1
    n_valid += int(valid)

    # Once/sec heartbeat so `valid` is visible during bring-up.
    now = time.time()
    if now - last_report >= 1.0:
      hz = n_published / (now - last_report)
      pct_valid = 100.0 * n_valid / max(n_published, 1)
      print(
        f"[bridge] {hz:5.1f} Hz  valid {pct_valid:3.0f}%  "
        f"mimic[vel_xy,z]={mimic[0]:.2f},{mimic[1]:.2f},{mimic[2]:.2f}  "
        f"dof0={mimic[6]:.3f}"
      )
      n_published = 0
      n_valid = 0
      last_report = now

    sleep_time = period - (time.time() - t_start)
    if sleep_time > 0:
      time.sleep(sleep_time)


if __name__ == "__main__":
  try:
    main()
  except KeyboardInterrupt:
    print("Interrupted.")
