# Pico Full-Body Teleop Pipeline — Replication Guide

This documents the **whole-body** Pico → G1 teleop pipeline: TWIST2's own GMR
retargeter driving TWIST2's pretrained whole-body tracking checkpoint
(`twist2_1017_20k.onnx`), on-robot via `g1_ctrl`'s `Twist2` FSM state.

This repo is self-contained,
built for a brand-new dev machine with nothing installed yet. The Pico
headset and the G1 robot are assumed already set up and unchanged from
their current working state. If you are looking to modify the Pico or G1 setup, see the linastens-sys/sparc repo for full comprehensive documentation. 

---
## 1. One-time software setup

Run once per machine. Skip anything already installed.

### 1a. Pico PC service (XRoboToolkit) — do this before `setup.sh`

The pybind build links against a library this installs.

```bash
# Download the .deb from the XR-Robotics/XRoboToolkit-PC-Service GitHub
# release if you don't already have it, then:
sudo dpkg -i XRoboToolkit_PC_Service_1.0.0_ubuntu_22.04_amd64.deb
```

Installs to `/opt/apps/roboticsservice/` as package `roboticsservice`. No
further config needed at install time — launching is a per-session step
(§2 below).

### 1b. Everything else

```bash
./setup.sh
```

Installs Python deps, installs `GMR` in editable mode, copies the Pico SDK
library out of the now-installed PC service, and builds the pybind
extension against it. Verify it built correctly:

```bash
uv run python Pico-Pybind/examples/example.py
```

---

## 2. Every-session Pico headset connection

The Pico connects to the dev machine over **USB, not WiFi**. This has to be redone each
session because the USB gadget interface name and IP drift between
connections.
1. **Start Headset, Cntrollers and Trackers:**
  - Turn headset and controllers on.
  - Strap tracking pucks to ankles. Calibrate them in the tracker app (you may need to un-pair and re-pair). 

2. **Launch the PC service** (does not auto-start):
   ```bash
   bash /opt/apps/roboticsservice/runService.sh
   ```
   Confirm it's listening: `ss -tulnp | grep 63901` should show
   `RoboticsService`.

3. **Plug the Pico in via USB.** Find the interface it created and its IP:
   ```bash
   ip -br addr
   ```
   Look for a `enx*`-style interface with a `192.168.x.x/24` address —
   **this name and IP change every session**, don't reuse a value from a
   previous run.

4. **Open the firewall for that interface** (scope it to the interface, not
   individual ports — the service negotiates dynamic RTC ports too):
   ```bash
   sudo ufw allow in on <your-enx-interface> && sudo ufw reload
   ```

5. **On the headset**: turn Pico WiFi **off**. In the XRoboToolkit app,
   connect to the PC's IP from step 2. Enable:
   - **Head** tracking
   - **Controller** tracking
   - **Full-body mode** (required — this is what streams the ankle data
     the GMR retargeter needs for leg tracking)
   - **Send**

6. **Verify live data** before touching the actual pipeline:
   ```bash
   uv run python Pico-Pybind/examples/example_body_tracking.py
   ```
   You should see left/right ankle (joints 7/8) and foot (10/11) poses
   updating live as you move. If everything reads all-zero, the headset is
   probably connected-but-not-streaming — double check Send is on and
   full-body mode is actually enabled (not just calibrated).

---

## 3. Sim workflow (`teleop-sim`)

No G1, no SSH, no DDS — this runs entirely on the dev machine, GMR
retargeting driving a simulated G1 over local Redis. Two terminals, both
from this repo's root:

**Terminal 1 — the simulated G1**, running the actual TWIST2 policy:
```bash
cd TWIST2/deploy_real
uv run python server_low_level_g1_sim.py \
  --xml ../assets/g1/g1_sim2sim_29dof.xml \
  --policy ../assets/ckpts/twist2_1017_20k.onnx \
  --device cpu \
  --policy_frequency 100 \
  --limit_fps 1
```



**Terminal 2 — the live Pico teleop**, publishing to the same local Redis:
```bash
cd TWIST2/deploy_real
uv run python xrobot_teleop_to_robot_w_hand.py \
  --robot unitree_g1 \
  --actual_human_height <YOUR_HEIGHT_METERS> \
  --redis_ip localhost \
  --target_fps 100 \
  --measure_fps 1
```

Replace `<YOUR_HEIGHT_METERS>` with your actual height (e.g. `1.75`) — this
scales the GMR retargeting to your body.

**On the headset**: press the **right controller's A button** (`key_one`)
to cycle the teleop state machine from idle into active teleop.

You should now see the simulated G1 (in Terminal 1's viewer window) mirror
your full-body motion, including legs/locomotion.

---

## 4. Real deployment (`teleop-real`)

### 4a. Power on the G1 and connect

Turn on the G1. Quick tap and hold the battery for three seconds. Listen for "Zero-force Mode" before launching g1_ctrl. You will also need the standard G1 controller, so turn that on too. 

Confirm network connectivity to the robot's onboard computer once it's up:
```bash
ping 192.168.123.164
```

### 4b. SSH in and launch the FSM controller

```bash
ssh unitree@192.168.123.164
```


Select 1 for foxy

```bash
cd ~/Documents/Physics-Based-Walker/deploy/robots/g1/build
./g1_ctrl --network=eth0
```

This starts the G1's FSM controller. Nothing else on the SDK/DDS side works
until this is running.


### 4c. Bring the robot to a safe standing state

On the physical controller: **L2 + Up** → `FixStand`. For controller commands, hold the first button while tapping the second once to switch between states.
### 4d. Enter the `Twist2 Teleop` FSM state

From `FixStand`: **R1 + A**.

**⚠️ Legs are live in this state.** Per the config comment in
`Physics-Based-Walker-main/deploy/robots/g1/config/config.yaml`: *"first
bring-up on a gantry"* — do not attempt a first-ever run of this state with
the robot standing freely on the ground. Use a gantry/support rig until
you've confirmed the mimic reference tracks sanely.

Until the dev-side bridge (next step) is running and delivering data, the
robot holds its default standing pose — `State_Twist2` logs "waiting for
first rt/mimic_obs frame" and just stands there. That's expected, not a
failure.

Exit paths from this state: **L2 + B** → `Passive` (safe stop, use this if
anything looks wrong), **R2 + A** → back to `FixStand` (Note: This is a different FixStand command than it was from the passive state).

### 4e. Dev machine: start the GMR teleop + DDS bridge

Two terminals (with the Pico already connected
and streaming per §2):

**Terminal 1 — TWIST2's GMR teleop**, same command as sim, `--redis_ip`
still `localhost` (it always writes to the *local* Redis; the bridge below
is what actually reaches the robot):
```bash
cd TWIST2/deploy_real
uv run python xrobot_teleop_to_robot_w_hand.py \
  --robot unitree_g1 \
  --actual_human_height <YOUR_HEIGHT_METERS> \
  --redis_ip localhost \
  --target_fps 100 \
  --measure_fps 1
```

**Terminal 2 — the Redis → DDS bridge** :
```bash
uv run python mimic_obs_bridge.py \
  --net <your-ethernet-interface-to-the-robot> \
  --robot-ip 192.168.123.164
```

`<your-ethernet-interface-to-the-robot>` is the dev machine's NIC connected
to the G1's network (e.g. `enp128s31f6` or `eno1` — check `ip -br addr`
again; this is a *different* interface than the Pico's USB one). The
bridge prints a heartbeat once a second:
```
[bridge] <hz> Hz  valid <pct>%  mimic[vel_xy,z]=...  dof0=...
```
`valid` should climb toward 100% once the teleop in Terminal 1 is actively
publishing (`valid=False` means the robot is falling back to holding its
default pose — check Terminal 1 is actually running and the Pico is still
streaming).

### 4f. Go live

Same as sim: on the headset, **right controller A button** to cycle into
active teleop. You may need to click reconnect in the XRoboToolkit app. Enusure the viewer shows the G1 in the starting position before switching to live teleop. Watch the robot closely — start with small, slow motions,
confirm arm tracking first, then confirm leg/stance tracking before doing
anything involving actual stepping.

**If anything looks wrong**: L2 + B on the controller → `Passive` immediately.

---

## 5. Troubleshooting

- **Pico shows all-zero poses**: check Send is ON and full-body mode is
  actually enabled in the app (not just the ankle trackers calibrated) —
  `is_body_data_available()` (what `example_body_tracking.py` checks)
  needs the headset to be actively streaming that block, which is a
  separate toggle from just having trackers strapped on.
- **`ufw`/firewall symptom**: app shows "TCP connection failed - socket not
  connected" even though `ping` to the PC works. Re-check `ufw status
  verbose` — the rule from a *previous* session's `enx*` interface name is
  silently stale once the interface changes.
- **DDS bridge shows `valid` stuck near 0%**: confirm Terminal 1
  (`xrobot_teleop_to_robot_w_hand.py`) is actually running and its Redis
  key (`action_body_unitree_g1_with_hands`) has a value —
  `redis-cli get action_body_unitree_g1_with_hands` should return
  something once it's live.
- **DDS bridge runs but the robot never receives anything**: cross-machine
  CycloneDDS discovery has needed explicit configuration before on this
  network (multicast doesn't reliably traverse the G1's link) —
  `mimic_obs_bridge.py` already pins the interface and adds an explicit
  unicast peer via `--net`/`--robot-ip`, so double-check those values are
  actually correct for your current network setup rather than assuming
  discovery "just works."
- **Robot's build seems out of date / behaves differently than expected**:
  `Physics-Based-Walker-main` isn't part of this repo (the robot's build is
  assumed already in place and working) — if you need to verify/rebuild it,
  that's tracked in the mjlab-adjacent `sparc` repo's curated copy, not
  here.

---

