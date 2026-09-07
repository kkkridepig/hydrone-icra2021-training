# Hydrone ICRA 2021 reproduction notes

## Phase 1 baseline

Captured on 2026-09-03 (Asia/Shanghai) after user confirmation of Phase 1.

Repository:

- Workspace: `/home/rosnoetic/hydrone_ws`
- Repository: `/home/rosnoetic/hydrone_ws/src/hydrone_deep_rl_icra`
- Branch: `icra2021-paper-reproduction`
- Baseline HEAD: `0f95846a3ae42a28cad5e45289fa986c59f7bdd9`
- Origin: `https://github.com/ricardoGrando/hydrone_deep_rl_icra.git`
- `origin/master` matched the baseline HEAD at audit time.

The working tree was already dirty before Phase 1. Existing tracked modifications,
untracked user files, malformed filename artifacts, and the independent
`rotors_simulator` HIL deletions are intentionally preserved. See
`repro_backups/20260903_phase1/BASELINE_HASHES.txt` and the Phase 0 audit for
the complete inventory.

## Evidence policy

- `CONFIRMED_FROM_CODE`: verified from local source, launch, XML/SDF, or generated Xacro.
- `CONFIRMED_FROM_PAPER`: only when explicitly stated by the user-provided prompt;
  the original paper was not independently fetched in Phase 0 because the network
  connection was reset.
- `NOT_PUBLICLY_SPECIFIED`: no reliable paper or repository value was found.
- Upstream quirks and reconstruction assumptions must remain separate from the paper
  configuration.

## Frozen interfaces

The existing Hydrone physics, RotorS Lee controller, UUV hydrodynamics, verified
Xacro, `velocity0.py`, reactive navigation, BC scripts, logger/demo files, multi-agent
code, Gym wrapper, and all existing user artifacts are protected. New reproduction
code must use a separate namespace/module and must not silently alter these interfaces.

Default ROS namespace for new parameterized code:

```text
/hydrone_aerial_underwater0
```

Observation contract to validate before training:

```text
20 LaserScan values + 3 previous physical action values
+ horizontal heading + vertical heading_z + 3-D goal distance
= state shape (26,)
```

Physical action contract:

```text
forward velocity: [0.00, 0.25] m/s
vertical velocity: [-0.25, 0.25] m/s
delta yaw: [-0.25, 0.25] rad
```

## User decisions recorded

The following decisions were confirmed before Phase 3:

1. Training goal Z distribution: `upstream_public` (`z=0.5..3.9`).
2. First version wind disturbance: disabled.
3. Reward-100 transition oversampling: retain threefold public-code behavior.
4. Formal seeds: paper seed count was not found; recommended local value is three
   sequential seeds `[0, 1, 2]` for the available 4 CPU / 9 GiB host.
5. Shared Stage 2 obstacle modification: explicitly permitted by the user; the
   tracked `models/obstacle_1/model.sdf` is changed to `<static>true</static>`.

Formal training remains blocked until the Phase 6 algorithm smoke tests pass.

## Phase 8 runner and stability gates

Before formal training, the paper runner was made operational without changing
the legacy controller path:

- `config/icra2021_stage1_formal.yaml` is a complete 1000-episode/500-step
  Stage 1 profile with seed `0`, `upstream_public` goals, wind disabled, and
  explicit DDPG/SAC reconstruction values.
- `scripts/icra2021_agent.py` recursively merges `base_config`, maps the
  algorithm-specific aliases (`ddpg_tau`/`sac_tau`, etc.), writes a manifest,
  JSONL episode records, moving-average rewards (window 300), periodic
  deterministic evaluation records, and wall/sim-time/RTF summaries.
- `episode_limit` and `step_limit` are explicit launch overrides. Bounded gate
  outputs use separate `gates/` directories and checkpoint names, so they do
  not contaminate the formal run.
- DDPG now exposes the same normalized/physical action conversion methods as
  SAC and stores checkpoint episode/metrics metadata for resume.

Phase 8 pre-run evidence recorded on 2026-09-04:

- 34 pure tests, Python compilation, YAML/XML parsing, `git diff --check`, and
  the smallest relevant catkin build passed.
- DDPG reset + one step and 100-step smoke passed (100 steps, finite bounded
  actions; RTF about `0.966`).
- DDPG 1000-environment-step stability gate passed: 5 episodes, 1000 steps,
  745 finite updates, checkpoint
  `/home/rosnoetic/hydrone_repro/icra2021/checkpoints/phase8_ddpg_stage1_seed0_step_1000.pt`.
  Four episodes collided under the exploratory policy; the final gate-limited
  episode ended at 139 steps without an environment terminal flag.
- SAC reset + one step and 100-step smoke passed (100 steps, finite bounded
  actions; RTF about `0.994`).
- SAC 1000-environment-step stability gate passed: 2 episodes, both 500-step
  time-limit truncations, 745 finite updates, alpha `0.0963536724`, replay
  size `1000`, checkpoint
  `/home/rosnoetic/hydrone_repro/icra2021/checkpoints/phase8_sac_stage1_seed0_step_1000.pt`.

The seed-0 formal Stage 1 runs are authorized next. Seeds `1` and `2` remain
pending a separate confirmation after both seed-0 runs finish; Stage 2 remains
out of scope for this phase.

Formal seed-0 DDPG was started with the operational profile and intentionally
paused at the first checkpoint boundary because the measured throughput was
about `0.9` RTF (roughly 40--50 wall seconds per exploratory episode). The
resume checkpoint is
`/home/rosnoetic/hydrone_repro/icra2021/checkpoints/phase8_ddpg_stage1_seed0.pt`
with `episode=10`, `global_step=1951`, `update_count=1696`; ten completed
episode records are in
`/home/rosnoetic/hydrone_repro/icra2021/runs/ddpg/stage_1/seed_0/episodes.jsonl`.
The checkpoint includes the in-progress episode's replay transitions, while
the episode log and resume counter advance only at completed episode
boundaries. The runner's ROS-shutdown path was tightened afterward so future
interruptions are reported as `interrupted=true` rather than as a fatal agent
error. SAC formal training and seeds `1,2` were not started.

## Phase 4 environment

Added the independent ROS-backed environment at
`hydrone_aerial_underwater_deep_rl/src/hydrone_icra2021/environment.py`.
The legacy `scripts/environment_3D.py` remains unchanged. The new environment
uses the existing `/hydrone_aerial_underwater0/{scan,ground_truth/odometry,cmd_vel}`
contract, keeps previous actions in physical units, and returns
`(observation, reward, terminated, truncated, info)` from `step()`.

Phase 4 evidence recorded on 2026-09-04:

- Pure contract/environment tests: 21 passed.
- Stage 1 ROS reset: state `(26,)`, finite; measured robot start was
  `(0.0, 0.0, 2.4802)` while the configured start remains `(0, 0, 2.5)`.
- Stage 1 bounded random rollout: 20 steps completed without premature
  termination or truncation.
- Goal event: `+100`, `terminated=False`, one goal replacement, robot
  displacement approximately `0.0056 m`.
- Time limit: an actual `max_steps=1` run returned `truncated=True` and
  `terminated=False`.
- Collision, default 500-step truncation, and evaluation `z=-1.0` safety
  semantics are covered by pure tests.

The environment module is intentionally not installed or wired into a launch
file yet; that integration is deferred to the paper launch-wrapper phase.

## Phase 5 DDPG smoke

Added the independent DDPG implementation at
`hydrone_aerial_underwater_deep_rl/src/hydrone_icra2021/ddpg.py` and its pure
tests. The smoke configuration is
`hydrone_aerial_underwater_deep_rl/config/icra2021_ddpg_smoke.yaml`; it is a
separate bounded profile and does not replace the paper configuration.

The agent stores normalized actions for the critic, converts them to physical
actions at the environment boundary, and preserves physical previous actions in
the 26-D observation. Replay records `terminated` and `truncated` separately;
time-limit transitions bootstrap. Checkpoints contain actor/critic and target
networks, both optimizers, replay, OU-noise state, RNG states, global/update
counters, contracts, config, and a run manifest.

Phase 5 evidence recorded on 2026-09-04:

- Pure contract, environment, replay, update, and checkpoint tests: 24 passed.
- Actual Stage 1 smoke: 5 episodes × 50-step limit, seed `0`, CPU, replay
  capacity `512`, batch `32`, warmup `32`, hidden width `512`.
- All five episodes reached the 50-step time limit with reward `0.0`,
  `terminated=False`, `truncated=True`.
- Replay size: `250`; finite optimizer updates: `219`; no non-finite metrics.
- Full resume checkpoint:
  `/home/rosnoetic/hydrone_repro/icra2021/checkpoints/phase5_ddpg_smoke.pt`
  (13,286,767 bytes), manifest HEAD
  `0f95846a3ae42a28cad5e45289fa986c59f7bdd9`.
- Independent restore check recovered `global_step=250`, `updates=219`, and
  `replay=250`; the restored agent completed one additional ROS environment
  step with a finite 26-D state and bounded physical action.

During the smoke, `GoalManager.delete()` was made idempotent for Gazebo's
explicit `model does not exist` response after `reset_world`; this prevents a
reset/goal lifecycle race without changing the legacy environment.

## Phase 6 SAC smoke

Added the independent SAC implementation at
`hydrone_aerial_underwater_deep_rl/src/hydrone_icra2021/sac.py`, its pure tests,
and the bounded configuration
`hydrone_aerial_underwater_deep_rl/config/icra2021_sac_smoke.yaml`.

The agent uses the paper-contract 3-layer, 512-unit policy, explicit twin-Q
networks, Gaussian reparameterization with tanh correction, automatic entropy
tuning, and `learning_rate=1e-3`. Policy/replay actions are normalized;
environment commands and previous-action observation fields remain physical.
The paper-accessible material does not fix twin-Q depth or SAC temperature
policy; these are therefore explicit, configurable reconstruction choices
(`critic_hidden_layers=2`, `tau=0.01`, `alpha=0.2`, automatic tuning with target
entropy `-3`) rather than claims about a reported paper value. The corresponding
sources are recorded as `NOT_PUBLICLY_SPECIFIED`/`upstream_default` in the YAML
scaffold.
Time-limit truncations bootstrap, collision terminations do not, and reward-100
transitions are stored three times to preserve the confirmed public-code
oversampling behavior. Checkpoints include policy, critic and target critic,
all optimizers, `log_alpha`, replay, RNG states, contracts, config, counters,
and manifest metadata.

Phase 6 evidence recorded on 2026-09-04:

- Pure contract/network/DDPG/environment/SAC tests: 27 passed.
- AST/YAML parsing and `git diff --check`: passed.
- Actual Stage 1 ROS smoke: 5 episodes × 50-step limit, seed `0`, CPU, replay
  capacity `512`, batch `32`, warmup `32`, hidden width `512`, wind disabled.
- All five episodes reached the 50-step time limit with reward `0.0`,
  `terminated=False`, `truncated=True`.
- Replay size: `250`; finite optimizer updates: `219`; no non-finite policy,
  Q, alpha, or metric values; final alpha `0.1622324139`.
- Full resume checkpoint:
  `/home/rosnoetic/hydrone_repro/icra2021/checkpoints/phase6_sac_smoke.pt`
  (15,624,124 bytes), manifest HEAD
  `0f95846a3ae42a28cad5e45289fa986c59f7bdd9`.
- Independent restore recovered `global_step=250`, `updates=219`,
  `replay=250`, alpha and optimizer states, deterministic evaluation produced
  identical normalized actions, and one additional ROS step returned a finite
  `(26,)` observation with bounded physical action.
- The smallest relevant catkin target,
  `catkin build hydrone_aerial_underwater_deep_rl --no-status`, succeeded.

At Phase 6 completion, formal training remained blocked until the user
explicitly approved the next phase; launch wrappers were intentionally deferred
until Phase 7.

## Phase 7 paper launch wrappers

Added a paper-specific launch path without changing the legacy launch files:

- `hydrone_aerial_underwater_deep_rl/launch/icra2021_simulation.launch` mirrors
  the stable single-vehicle Stage 1/2 setup but uses a parameterized bridge.
- `hydrone_aerial_underwater_deep_rl/launch/icra2021_paper.launch` exposes
  `algorithm`, `stage`, `mode`, `gui`, `paused`, `namespace`, `config`,
  `checkpoint`, and an explicit `run_agent` opt-in.
- `scripts/icra2021_velocity_bridge.py` preserves the existing body-forward
  velocity/yaw-rotation and trajectory topic semantics while parameterizing the
  namespace.
- `scripts/icra2021_publisher_guard.py` queries the ROS master publisher
  registry and is a required node; any competing `/namespace/cmd_vel`
  publisher causes fail-fast shutdown of the complete wrapper. It uses wall
  time so the check runs even when Gazebo is paused.
- `scripts/icra2021_agent.py` is conditionally launched only with
  `run_agent:=true`; it provides train/evaluate/resume dispatch for the new
  DDPG/SAC agents, resumes the checkpoint episode counter, and requires
  explicit config/checkpoint paths as appropriate.
  The default wrapper invocation never launches this node.

Phase 7 evidence recorded on 2026-09-04:

- Python compilation, AST/YAML parsing, `git diff --check`, and the focused
  publisher-guard tests passed (full suite: 30 tests).
- `roslaunch --nodes`, `--files`, and `--dump-params` parse checks passed for
  Stage 1/2 wrapper paths; `run_agent:=true` includes the agent node only when
  explicitly requested.
- Short runtime smoke with
  `algorithm=sac stage=1 mode=evaluate gui=false paused=true run_agent=false`
  started Gazebo Classic 11, Lee controller, parameterized bridge, and guard.
  `/cmd_vel` had no publisher and one bridge subscriber; bridge published
  `/command/trajectory`, consumed by the Lee controller. Guard reported PASS.
- A temporary external `rostopic pub` on `/hydrone_aerial_underwater0/cmd_vel`
  was detected; guard reported FATAL and required roslaunch shut down all
  simulation nodes. No training agent was started.
- `catkin build hydrone_aerial_underwater_deep_rl --no-status` succeeded.

All Phase 7 runtime processes are stopped. Formal Stage 1/2 training and
evaluation remain unstarted pending explicit approval and a final review of the
agent runner/configuration contract.

## Phase 1 scope

Phase 1 only records the baseline and creates non-operative configuration
scaffolding. It does not alter the existing ROS graph, launch behavior, model files,
Python environment, or training entry points.
