"""Pure NumPy contracts shared by collection, learning, deployment and tests.

This pilot keeps the original Hydrone hydrodynamics. The intervention changes
the UUV damping coefficient through its real ROS service, not a policy input.
Ground truth, intervention flags and random seeds NEVER enter model features.
"""
import hashlib
import json
import math
from pathlib import Path

import numpy as np

BASE_SHA = "8790a9ea1598c779e38e1fa5eeb3eeec6ca87bcf"
ACTION_SCALE = np.array([0.25, 0.25, 0.25], dtype=np.float32)
HISTORY_DIM = 19
EXPERT_DIM = 21


def load_config(path):
    c = json.loads(Path(path).read_text())
    for key in ("dt", "history", "horizon", "batch_size", "episode_seconds"):
        if c[key] <= 0:
            raise ValueError("Nonpositive " + key)
    if c["bridge"] not in ("legacy", "yaw_rate"):
        raise ValueError("Unknown bridge semantics")
    groups = [set(c[k]) for k in ("train_seeds", "validation_seeds", "test_seeds")]
    if any(groups[i] & groups[j] for i in range(3) for j in range(i)):
        raise ValueError("Train/validation/test scenario seeds must be disjoint")
    if c["image_size"] < 16 or c["dt"] < 0.05:
        raise ValueError("Unsupported camera size or command period")
    if c["water_z"] != 0 or c["camera_rate"] != 10:
        raise ValueError("This asset profile fixes water_z=0 and camera_rate=10; change assets before changing these")
    for key in ("train_seeds", "validation_seeds", "test_seeds", "model_seeds"):
        if not c[key] or len(set(c[key])) != len(c[key]) or any(s < 0 for s in c[key]):
            raise ValueError("Empty, duplicate or negative seeds: " + key)
    if not 0 < c["risk_threshold"] < 1:
        raise ValueError("Invalid risk threshold")
    if c["conditions"] != ["clean", "visual", "dynamics", "both"]:
        raise ValueError("All four factorial conditions are required")
    if c["methods"] != ["teacher", "short", "long", "unified", "separated"]:
        raise ValueError("All five evaluation methods are required")
    return c


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def wrap(a):
    return (a + math.pi) % (2.0 * math.pi) - math.pi


def rpy(q):
    x, y, z, w = np.asarray(q, dtype=float)
    norm = np.linalg.norm(q)
    if not np.isfinite(norm) or norm < 1e-8:
        raise ValueError("Invalid quaternion")
    x, y, z, w = np.asarray(q, dtype=float) / norm
    return np.array([
        math.atan2(2*(w*x+y*z), 1-2*(x*x+y*y)),
        math.asin(float(np.clip(2*(w*y-z*x), -1, 1))),
        math.atan2(2*(w*z+x*y), 1-2*(y*y+z*z))
    ])


def rotate(q, v):
    q = np.asarray(q, dtype=float)
    q /= np.linalg.norm(q)
    xyz = q[:3]
    v = np.asarray(v, dtype=float)
    return v + 2*np.cross(xyz, np.cross(xyz, v) + q[3]*v)


def action_clip(u):
    u = np.asarray(u, dtype=np.float32)
    if u.shape != (3,) or not np.isfinite(u).all():
        raise ValueError("Action must be three finite physical references")
    return np.clip(u, [0, -0.25, -0.25], [0.25, 0.25, 0.25]).astype(np.float32)


def scenario(seed, direction, c, split="train"):
    if direction not in ("air_to_water", "water_to_air"):
        raise ValueError(direction)
    rng = np.random.RandomState(seed * 17 + (0 if direction == "air_to_water" else 1))
    start = np.array([rng.uniform(-0.6, 0.0), rng.uniform(-0.3, 0.3),
                      rng.uniform(0.65, 0.95)])
    goal = start + np.array([rng.uniform(0.45, 0.85), rng.uniform(-0.15, 0.15), 0])
    goal[2] = rng.uniform(-0.75, -0.55)
    if direction == "water_to_air":
        start[2], goal[2] = goal[2], start[2]
    test = split == "test"
    lo, hi = c["damping_test_range" if test else "damping_train_range"]
    vlo, vhi = c["visual_bias_test" if test else "visual_bias_train"]
    return dict(seed=int(seed), direction=direction, split=split,
                start=start.tolist(), goal=goal.tolist(), yaw=float(rng.uniform(-0.12, 0.12)),
                damping=float(rng.uniform(lo, hi)),
                visual_strength=float(rng.uniform(vlo, vhi)),
                visual_shift=float(rng.uniform(*c["visual_shift_range"])),
                perturb_center=float(rng.uniform(-0.08, 0.08)))


def interventions(z, spec, condition, c):
    dyn = condition in ("dynamics", "both") and abs(z-spec["perturb_center"]) < c["perturb_band"]
    vis = condition in ("visual", "both") and abs(
        z-spec["perturb_center"]-spec["visual_shift"]) < c["perturb_band"]
    return bool(vis), bool(dyn), spec["damping"] if dyn else c["nominal_damping"]


def resize_rgb(image, size):
    """Dependency-free nearest-neighbour sampling; no torchvision/cv_bridge."""
    image = np.asarray(image)
    if image.ndim != 3 or image.shape[2] != 3 or image.dtype != np.uint8:
        raise ValueError("Expected uint8 RGB")
    yy = np.linspace(0, image.shape[0]-1, size).astype(int)
    xx = np.linspace(0, image.shape[1]-1, size).astype(int)
    return np.ascontiguousarray(image[yy[:, None], xx[None, :], :])


def decode_rgb(height, width, step, encoding, buffer):
    channels = {"rgb8": 3, "bgr8": 3, "rgba8": 4, "bgra8": 4, "mono8": 1}
    if encoding not in channels:
        raise ValueError("Unsupported RGB encoding: " + encoding)
    n = channels[encoding]
    data = np.frombuffer(bytes(buffer), dtype=np.uint8)
    if height <= 0 or width <= 0 or len(data) != height*step or step < width*n:
        raise ValueError("Invalid image dimensions/stride")
    data = data.reshape(height, step)[:, :width*n].reshape(height, width, n)
    if n == 1:
        return np.repeat(data, 3, axis=2).copy()
    data = data[:, :, :3]
    if encoding in ("bgr8", "bgra8"):
        data = data[:, :, ::-1]
    return data.copy()


def write_png(path, image):
    """Write RGB evidence without adding Pillow/OpenCV/torchvision."""
    import struct
    import zlib
    rgb = np.asarray(image, dtype=np.uint8)
    height, width, channels = rgb.shape
    if channels != 3:
        raise ValueError("PNG requires RGB")
    def chunk(tag, value):
        return struct.pack(">I", len(value))+tag+value+struct.pack(">I", zlib.crc32(tag+value) & 0xffffffff)
    raw = b"".join(b"\x00"+row.tobytes() for row in rgb)
    content = b"\x89PNG\r\n\x1a\n"
    content += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    content += chunk(b"IDAT", zlib.compress(raw))
    content += chunk(b"IEND", b"")
    Path(path).write_bytes(content)


def corrupt_image(image, strength, rng):
    """Controlled haze/noise/occlusion, NOT a physical refraction renderer."""
    x = image.astype(np.float32) / 255
    # Continuously varying corruption: no artificial condition ID/color patch.
    a = float(strength)
    color = rng.uniform(0.3, 0.8, size=(1, 1, 3))
    x = (1-2*a)*x + 2*a*color + rng.normal(0, a*0.1, x.shape)
    if rng.rand() < 0.35:
        h, w = x.shape[:2]
        y, xx = rng.randint(0, h//2), rng.randint(0, w//2)
        x[y:y+h//3, xx:xx+w//3] = color
    return (np.clip(x, 0, 1)*255).astype(np.uint8)


def sense(state, rng, c):
    """Explicit simulation odometry proxy. No absolute z is exposed.

    Assumes an external velocity/attitude/XY estimator of the stated quality.
    This is not a real visual-inertial estimator or sim-to-real validation.
    Vector: XY(2), RPY(3), world velocity(3), body gyro(3), IMU accel(3).
    """
    return np.concatenate([
        np.asarray(state["position"][:2]) + rng.normal(0, c["xy_noise_std"], 2),
        np.asarray(state["rpy"]) + rng.normal(0, c["attitude_noise_std"], 3),
        np.asarray(state["velocity"]) + rng.normal(0, c["velocity_noise_std"], 3),
        state["gyro"], state["accel"]
    ]).astype(np.float32)


def history_row(sensor, previous_action, dt):
    # No XY, goal, height, image, condition ID, clock or injection parameter.
    out = np.concatenate([sensor[2:14], previous_action / ACTION_SCALE,
                          [dt, 1.0], sensor[5:7] / 0.25]).astype(np.float32)
    assert out.shape == (HISTORY_DIM,)
    return np.clip(out, -20, 20)


def history_at(rows, i, length):
    out = np.zeros((length, HISTORY_DIM), dtype=np.float32)
    part = rows[max(0, i-length+1):i+1]
    out[-len(part):] = part
    return out


def expert_features(sensor, height, goal, previous_action, dynamics_probability):
    roll, pitch, yaw = sensor[2:5]
    dxy = np.asarray(goal[:2]) - sensor[:2]
    dx = math.cos(yaw)*dxy[0] + math.sin(yaw)*dxy[1]
    dy = -math.sin(yaw)*dxy[0] + math.cos(yaw)*dxy[1]
    out = np.concatenate([
        [dx, dy, goal[2]-height, height, math.sin(roll), math.sin(pitch)],
        sensor[5:14], previous_action / ACTION_SCALE,
        [dynamics_probability, np.linalg.norm(dxy), float(goal[2] > 0)]
    ]).astype(np.float32)
    assert out.shape == (EXPERT_DIM,)
    return np.clip(out, -20, 20)


def teacher(state, goal):
    """State oracle for bounded goal tracking, not an RL policy."""
    p, v = np.asarray(state["position"]), np.asarray(state["velocity"])
    dx, dy, dz = np.asarray(goal)-p
    distance_xy = math.hypot(dx, dy)
    heading = wrap(math.atan2(dy, dx)-state["rpy"][2]) if distance_xy > 0.05 else 0.0
    forward = np.clip(0.8*distance_xy, 0, 0.23) * max(0, math.cos(heading))**2
    if abs(heading) > 0.8:
        forward = 0.0
    # Velocity feedforward compensation handles strong legacy damping only
    # within the existing command bounds. Failure must trip the stage-1 gate.
    vertical = np.clip(1.2*dz - 0.25*v[2], -0.24, 0.24)
    return action_clip([forward, vertical, np.clip(1.4*heading, -0.25, 0.25)])


class HeightFilter:
    def __init__(self, gain):
        self.height = None
        self.gain = gain

    def update(self, visual_height, vz, dt, bad_probability, method):
        if self.height is None:
            self.height = float(visual_height)
        else:
            predicted = self.height + float(vz)*dt
            weight = self.gain
            if method in ("separated", "unified"):
                weight *= 1-float(np.clip(bad_probability, 0, 1))
            self.height = predicted + weight*float(np.clip(visual_height-predicted, -0.6, 0.6))
        return float(self.height)


class ChunkExecutor:
    """Consume each row once; never repeat the first action to mimic a chunk."""
    def __init__(self, method, c):
        self.method, self.c = method, c
        self.chunk, self.index = None, 0

    def needs_plan(self, visual_bad, dynamics_bad, height, vz):
        if self.chunk is None or self.index >= len(self.chunk):
            return True
        if self.method == "short":
            return True
        if self.method == "long":
            return False
        risk = max(visual_bad, dynamics_bad) if self.method == "unified" else dynamics_bad
        # Common geometric guard, identical in both adaptive methods.
        near = abs(height) < self.c["interface_guard_distance"] and abs(vz) > 0.025
        return risk >= self.c["risk_threshold"] or near

    def replace(self, chunk):
        a = np.asarray(chunk, dtype=np.float32)
        if a.shape != (self.c["horizon"], 3) or not np.isfinite(a).all():
            raise ValueError("Invalid predicted action chunk")
        self.chunk = np.stack([action_clip(u) for u in a])
        self.index = 0

    def next(self):
        if self.chunk is None or self.index >= len(self.chunk):
            raise RuntimeError("Plan required")
        a = self.chunk[self.index].copy()
        self.index += 1
        return a


def decision_probs(pv, pd, method):
    # Single-score ablation conservatively applies the union to both decisions.
    if method == "unified":
        union = max(pv, pd)
        return union, union
    return pv, pd


def terminal_reason(state, goal, elapsed, c):
    p = np.asarray(state["position"])
    if not np.isfinite(np.concatenate([p, state["velocity"], state["rpy"]])).all():
        return "nonfinite"
    if p[2] < c["z_bounds"][0] or p[2] > c["z_bounds"][1]:
        return "height_boundary"
    if np.max(np.abs(p[:2])) > c["xy_bound"]:
        return "xy_boundary"
    if state["min_scan"] < c["laser_stop"]:
        return "laser_proximity"
    if np.max(np.abs(state["rpy"][:2])) > c["max_tilt"]:
        return "tilt"
    if np.linalg.norm(state["gyro"]) > c["max_rate"]:
        return "angular_rate"
    if elapsed >= c["episode_seconds"]:
        return "timeout"
    return None


def success_sample(state, goal, c):
    return (np.linalg.norm(np.asarray(state["position"])-goal) <= c["goal_tolerance"]
            and np.linalg.norm(state["velocity"]) < 0.12
            and np.max(np.abs(state["rpy"][:2])) < 0.2)


def json_write(path, obj):
    Path(path).write_text(json.dumps(obj, indent=2, ensure_ascii=False, allow_nan=False))
