"""FP32 only; uses the image-provided torch, never installs any dependency."""
import json
import os
import random
import time
from pathlib import Path

import numpy as np
import torch
from torch import nn

from core import (ACTION_SCALE, EXPERT_DIM, HISTORY_DIM, HeightFilter, canonical_hash,
                  decision_probs, expert_features, history_at, json_write)


class EventModel(nn.Module):
    def __init__(self, c):
        super().__init__()
        self.vision = nn.Sequential(
            nn.Conv2d(3, 12, 5, stride=2, padding=2), nn.ReLU(),
            nn.Conv2d(12, 24, 3, stride=2, padding=1), nn.ReLU(),
            nn.AdaptiveAvgPool2d((3, 3)), nn.Flatten(),
            nn.Linear(216, 64), nn.ReLU(), nn.Linear(64, 2))
        self.motion = nn.Sequential(
            nn.Flatten(), nn.Linear(c["history"]*HISTORY_DIM, 96), nn.ReLU(),
            nn.Linear(96, 48), nn.ReLU(), nn.Linear(48, 1))

    def forward(self, image, history):
        visual = self.vision(image)
        return visual[:, 0], torch.cat([visual[:, 1:2], self.motion(history)], dim=1)


class ActionModel(nn.Module):
    def __init__(self, c):
        super().__init__()
        self.horizon = c["horizon"]
        self.net = nn.Sequential(nn.Linear(EXPERT_DIM, 128), nn.ReLU(),
                                 nn.Linear(128, 128), nn.ReLU(),
                                 nn.Linear(128, c["horizon"]*3))

    def forward(self, x):
        raw = self.net(x).reshape(-1, self.horizon, 3)
        return torch.stack([torch.sigmoid(raw[:, :, 0]),
                            torch.tanh(raw[:, :, 1]), torch.tanh(raw[:, :, 2])], dim=2)


def tensor(x, device):
    return torch.as_tensor(np.asarray(x), dtype=torch.float32, device=device)


def seed_all(seed):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    torch.set_num_threads(2)


def accelerator_check(c, device):
    if device.startswith("cuda") and not torch.cuda.is_available():
        raise RuntimeError("PPU unavailable through torch.cuda; keep vendor torch unchanged")
    if device.startswith("cuda") and os.environ.get("HYDRONE_BACKEND") == "ppu":
        if "PPU" not in torch.cuda.get_device_name(0).upper():
            raise RuntimeError("HYDRONE_BACKEND=ppu but device is not identified as a PPU")
        if ".venvs" in str(Path(torch.__file__).resolve()):
            raise RuntimeError("torch is installed inside the venv; verify vendor runtime inheritance")
    model = EventModel(c).to(device)
    expert = ActionModel(c).to(device)
    optimizer = torch.optim.Adam(list(model.parameters())+list(expert.parameters()), lr=1e-4)
    im = torch.randn(4, 3, c["image_size"], c["image_size"], device=device)
    hist = torch.randn(4, c["history"], HISTORY_DIM, device=device)
    h, logits = model(im, hist)
    action = expert(torch.randn(4, EXPERT_DIM, device=device))
    loss = h.square().mean()+logits.square().mean()+action.square().mean()
    loss.backward()
    if not all(p.grad is None or bool(torch.isfinite(p.grad).all()) for p in model.parameters()):
        raise RuntimeError("Nonfinite accelerator gradients")
    optimizer.step()
    if device.startswith("cuda"):
        torch.cuda.synchronize()
    return dict(device=device, name=torch.cuda.get_device_name(0) if device.startswith("cuda") else "CPU",
                torch=torch.__version__, torch_path=torch.__file__, fp32_event_and_expert_update=True)


def load_episodes(collection):
    root = Path(collection)
    episodes = []
    for path in sorted((root/"episodes").glob("*.npz")):
        meta = json.loads(path.with_suffix(".json").read_text())
        with np.load(path, allow_pickle=False) as f:
            data = {k: f[k] for k in f.files}
        if len(data["images"]) < 2:
            continue
        episodes.append((meta, data))
    if not episodes:
        raise RuntimeError("No complete collected episodes")
    return episodes


def event_arrays(episodes, seeds, c):
    selected = [(m, d) for m, d in episodes if m["scenario"]["seed"] in seeds]
    if not selected:
        raise RuntimeError("Empty scenario split")
    images, histories, heights, labels = [], [], [], []
    for meta, d in selected:
        for i in range(len(d["images"])):
            images.append(d["images"][i])
            histories.append(history_at(d["history_rows"], i, c["history"]))
        heights.append(d["heights"])
        labels.append(d["labels"])
    return dict(images=np.asarray(images, dtype=np.uint8),
                histories=np.asarray(histories, dtype=np.float32),
                heights=np.concatenate(heights).astype(np.float32),
                labels=np.concatenate(labels).astype(np.float32))


def event_batch(arrays, ids, device):
    im = tensor(arrays["images"][ids].transpose(0, 3, 1, 2)/255.0, device)
    return im, tensor(arrays["histories"][ids], device)


def metrics_binary(y, p):
    y, p = np.asarray(y), np.asarray(p)
    positive, negative = y > 0.5, y <= 0.5
    tp, fp = int(np.sum((p >= .5) & positive)), int(np.sum((p >= .5) & negative))
    recall = tp/max(1, int(positive.sum()))
    specificity = 1-fp/max(1, int(negative.sum()))
    return dict(brier=float(np.mean((p-y)**2)),
                prevalence=float(y.mean()), recall=recall, specificity=specificity,
                balanced_accuracy=(recall+specificity)/2,
                positives=int(positive.sum()), negatives=int(negative.sum()))


def evaluate_events(model, arrays, device, batch_size):
    model.eval()
    hh, pp = [], []
    with torch.no_grad():
        for start in range(0, len(arrays["images"]), batch_size):
            ids = np.arange(start, min(start+batch_size, len(arrays["images"])))
            h, logits = model(*event_batch(arrays, ids, device))
            hh.extend(h.cpu().numpy())
            pp.extend(torch.sigmoid(logits).cpu().numpy())
    hh, pp = np.asarray(hh), np.asarray(pp)
    return dict(height_mae_m=float(np.mean(abs(hh-arrays["heights"]))),
                visual=metrics_binary(arrays["labels"][:, 0], pp[:, 0]),
                dynamics=metrics_binary(arrays["labels"][:, 1], pp[:, 1]))


def predict_episode(model, data, c, device):
    arr = dict(images=data["images"],
               histories=np.asarray([history_at(data["history_rows"], i, c["history"])
                                     for i in range(len(data["images"]))]))
    hs, ps = [], []
    with torch.no_grad():
        for start in range(0, len(arr["images"]), c["batch_size"]):
            ids = np.arange(start, min(len(arr["images"]), start+c["batch_size"]))
            h, logits = model(*event_batch(arr, ids, device))
            hs.extend(h.cpu().numpy())
            ps.extend(torch.sigmoid(logits).cpu().numpy())
    return np.asarray(hs), np.asarray(ps)


def action_arrays(model, episodes, seeds, c, device):
    features, targets, masks = [], [], []
    used = 0
    for meta, d in episodes:
        if meta["scenario"]["seed"] not in seeds or meta["reason"] != "success":
            continue
        used += 1
        heights, probabilities = predict_episode(model, d, c, device)
        # Train one common action expert on estimated-state distributions of all
        # filter variants. Every evaluated method uses the identical weights.
        for method in ("short", "unified", "separated"):
            filt = HeightFilter(c["filter_gain"])
            for i in range(len(heights)):
                pv, pd = decision_probs(*probabilities[i], method)
                sensor = d["sensors"][i]
                h = filt.update(heights[i], sensor[7], d["input_dt"][i], pv, method)
                previous = np.zeros(3) if i == 0 else d["actions"][i-1]
                features.append(expert_features(sensor, h, meta["scenario"]["goal"], previous, pd))
                n = min(c["horizon"], len(heights)-i)
                target = np.zeros((c["horizon"], 3), dtype=np.float32)
                target[:n] = d["actions"][i:i+n]/ACTION_SCALE
                mask = np.zeros((c["horizon"], 3), dtype=np.float32)
                mask[:n] = 1
                targets.append(target)
                masks.append(mask)
    if used == 0:
        raise RuntimeError("No successful teacher episodes in this split; cannot fit an action expert")
    return dict(x=np.asarray(features, dtype=np.float32), y=np.asarray(targets, dtype=np.float32),
                mask=np.asarray(masks, dtype=np.float32), successful_episodes=used)


def fit(collection, output, c, seed, device="cuda:0"):
    out = Path(output)
    out.mkdir(parents=True, exist_ok=False)
    seed_all(seed)
    json_write(out/"accelerator.json", accelerator_check(c, device))
    episodes = load_episodes(collection)
    train = event_arrays(episodes, set(c["train_seeds"]), c)
    val = event_arrays(episodes, set(c["validation_seeds"]), c)
    model = EventModel(c).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=c["learning_rate"])
    rng = np.random.RandomState(seed)
    prevalence = train["labels"].mean(axis=0)
    if np.any(prevalence < c["gate_min_label_fraction"]) or np.any(prevalence > 0.99):
        raise RuntimeError("Insufficient positive/negative event coverage; inspect collection")
    pos_weight = tensor(np.clip((1-prevalence)/prevalence, 1, 12), device)
    best, best_state = float("inf"), None
    started = time.monotonic()
    with (out/"training.jsonl").open("w") as log:
        for epoch in range(c["perception_epochs"]):
            model.train()
            ids = rng.permutation(len(train["images"]))
            losses = []
            for start in range(0, len(ids), c["batch_size"]):
                ix = ids[start:start+c["batch_size"]]
                h, logits = model(*event_batch(train, ix, device))
                loss = (nn.functional.smooth_l1_loss(h, tensor(train["heights"][ix], device))
                        + nn.functional.binary_cross_entropy_with_logits(
                            logits, tensor(train["labels"][ix], device), pos_weight=pos_weight))
                if not bool(torch.isfinite(loss)):
                    raise RuntimeError("Nonfinite event loss")
                optimizer.zero_grad()
                loss.backward()
                nn.utils.clip_grad_norm_(model.parameters(), 5)
                optimizer.step()
                losses.append(float(loss.item()))
            metric = evaluate_events(model, val, device, c["batch_size"])
            score = metric["height_mae_m"]+metric["visual"]["brier"]+metric["dynamics"]["brier"]
            if score < best:
                best = score
                best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
            row = dict(stage="events", epoch=epoch, train_loss=float(np.mean(losses)), validation=metric)
            log.write(json.dumps(row)+"\n")
            log.flush()
            print(json.dumps(row), flush=True)
        model.load_state_dict(best_state)
        model.eval()
        a_train = action_arrays(model, episodes, set(c["train_seeds"]), c, device)
        a_val = action_arrays(model, episodes, set(c["validation_seeds"]), c, device)
        expert = ActionModel(c).to(device)
        optimizer = torch.optim.Adam(expert.parameters(), lr=c["learning_rate"])
        best_action, action_state = float("inf"), None
        for epoch in range(c["expert_epochs"]):
            expert.train()
            ids = rng.permutation(len(a_train["x"]))
            losses = []
            for start in range(0, len(ids), c["batch_size"]):
                ix = ids[start:start+c["batch_size"]]
                prediction = expert(tensor(a_train["x"][ix], device))
                mask = tensor(a_train["mask"][ix], device)
                loss = ((prediction-tensor(a_train["y"][ix], device)).abs()*mask).sum()/mask.sum()
                if not bool(torch.isfinite(loss)):
                    raise RuntimeError("Nonfinite action loss")
                optimizer.zero_grad()
                loss.backward()
                nn.utils.clip_grad_norm_(expert.parameters(), 5)
                optimizer.step()
                losses.append(float(loss.item()))
            expert.eval()
            numerator, denominator = 0.0, 0.0
            with torch.no_grad():
                for start in range(0, len(a_val["x"]), c["batch_size"]):
                    sl = slice(start, start+c["batch_size"])
                    mask = tensor(a_val["mask"][sl], device)
                    error = (expert(tensor(a_val["x"][sl], device))-tensor(a_val["y"][sl], device)).abs()
                    numerator += float((error*mask).sum().item())
                    denominator += float(mask.sum().item())
            validation = numerator/denominator
            if validation < best_action:
                best_action = validation
                action_state = {k: v.detach().cpu().clone() for k, v in expert.state_dict().items()}
            row = dict(stage="expert", epoch=epoch, train_l1=float(np.mean(losses)), validation_l1=validation)
            log.write(json.dumps(row)+"\n")
            log.flush()
            print(json.dumps(row), flush=True)
    checkpoint = dict(schema=1, config=c, config_hash=canonical_hash(c), model_seed=seed,
                      event_state=best_state, action_state=action_state)
    torch.save(checkpoint, out/"model.pt")
    # Same-process CPU roundtrip verifies serialization and deployment forward.
    restored = Predictor(out/"model.pt", c)
    restored.events(train["images"][0], train["histories"][0])
    restored.actions(a_train["x"][0])
    result = dict(model_seed=seed, train_seconds=time.monotonic()-started,
                  events_validation=evaluate_events(model, val, device, c["batch_size"]),
                  expert_validation_normalized_l1=best_action,
                  action_training_successful_episodes=a_train["successful_episodes"],
                  checkpoint_roundtrip=True, training_device=device, inference_device="cpu")
    json_write(out/"fit.json", result)
    return result


class Predictor:
    def __init__(self, checkpoint, c):
        torch.set_num_threads(2)
        # Load only self-generated/trusted experiment checkpoints.
        data = torch.load(str(checkpoint), map_location="cpu", weights_only=False)
        if data["schema"] != 1 or data["config_hash"] != canonical_hash(c):
            raise RuntimeError("Checkpoint/config mismatch")
        self.events_model = EventModel(c)
        self.expert = ActionModel(c)
        self.events_model.load_state_dict(data["event_state"])
        self.expert.load_state_dict(data["action_state"])
        self.events_model.eval()
        self.expert.eval()
        self.seed = data["model_seed"]
        # Warm up CPU kernels before any timed episode, equally for all methods.
        self.events(np.zeros((c["image_size"], c["image_size"], 3), dtype=np.uint8),
                    np.zeros((c["history"], HISTORY_DIM), dtype=np.float32))
        self.actions(np.zeros(EXPERT_DIM, dtype=np.float32))

    def events(self, image, history):
        with torch.no_grad():
            im = tensor(image.transpose(2, 0, 1)[None]/255.0, "cpu")
            h, logits = self.events_model(im, tensor(history[None], "cpu"))
            height = float(h.item())
            probabilities = torch.sigmoid(logits)[0].numpy()
        if not np.isfinite(np.r_[height, probabilities]).all():
            raise RuntimeError("Nonfinite event prediction")
        return height, float(probabilities[0]), float(probabilities[1])

    def actions(self, features):
        with torch.no_grad():
            out = self.expert(tensor(features[None], "cpu"))[0].numpy()*ACTION_SCALE
        if not np.isfinite(out).all():
            raise RuntimeError("Nonfinite expert action")
        return out
