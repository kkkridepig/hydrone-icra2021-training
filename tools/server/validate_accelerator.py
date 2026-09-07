#!/usr/bin/env python3
"""ROS imports plus bounded CUDA updates using the project's actual agents."""
import gc
import json
from pathlib import Path
import sys
import tempfile
import time

import numpy as np
import torch
import rospy
import tf
from gazebo_msgs.srv import GetWorldProperties
from sensor_msgs.msg import LaserScan
from nav_msgs.msg import Odometry
from mav_msgs.msg import Actuators

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src/hydrone_deep_rl_icra/hydrone_aerial_underwater_deep_rl/scripts'))
from icra2021_agent import _training_config, _load_config
from hydrone_icra2021.ddpg import DDPGAgent, DDPGConfig
from hydrone_icra2021.sac import SACAgent, SACConfig

assert torch.cuda.is_available(), 'CUDA is unavailable'
torch.set_num_threads(2)
data = _load_config(str(ROOT / 'src/hydrone_deep_rl_icra/hydrone_aerial_underwater_deep_rl/config/icra2021_stage1_formal.yaml'))
data['training']['device'] = 'cuda:0'
(ROOT / 'logs/server').mkdir(parents=True, exist_ok=True)
report = {'python': sys.executable, 'torch': torch.__version__,
          'cuda': torch.version.cuda, 'gpu': torch.cuda.get_device_name(0),
          'ros_imports': 'passed', 'algorithms': {}}
for name, agent_class, config_class in [('ddpg', DDPGAgent, DDPGConfig), ('sac', SACAgent, SACConfig)]:
    torch.cuda.empty_cache()
    torch.cuda.reset_peak_memory_stats()
    config = config_class(**_training_config(name, data))
    assert config.device == 'cuda:0'
    agent = agent_class(config)
    module = agent.actor if name == 'ddpg' else agent.policy
    assert next(module.parameters()).is_cuda
    before = next(module.parameters()).detach().clone()
    rng = np.random.RandomState(0)
    started = time.monotonic()
    for i in range(config.warmup_steps + 4):
        state = rng.uniform(-0.1, 0.1, 26).astype(np.float32)
        next_state = rng.uniform(-0.1, 0.1, 26).astype(np.float32)
        action = rng.uniform(-1, 1, 3).astype(np.float32)
        metrics = agent.observe(state, action, 0.1, next_state, False, False)
        if metrics:
            assert all(np.isfinite(v) for v in metrics.values()), metrics
    torch.cuda.synchronize()
    assert agent.update_count > 0
    assert not torch.equal(before, next(module.parameters()).detach()), 'policy parameters did not update'
    assert all(torch.isfinite(p).all() for p in module.parameters())
    with tempfile.TemporaryDirectory(prefix='gpu-checkpoint-', dir=str(ROOT / 'logs/server')) as temp:
        checkpoint = str(Path(temp) / (name + '.pt'))
        agent.save_checkpoint(checkpoint, manifest={'validation': 'synthetic_cuda'}, stage=1, episode=0)
        restored = agent_class(config)
        restored.load_checkpoint(checkpoint)
        other = restored.actor if name == 'ddpg' else restored.policy
        assert next(other.parameters()).is_cuda
        for a, b in zip(module.parameters(), other.parameters()):
            torch.testing.assert_close(a, b, rtol=0, atol=0)
        assert restored.update_count == agent.update_count
    report['algorithms'][name] = {
        'updates': agent.update_count, 'hidden_dim': config.hidden_dim,
        'batch_size': config.batch_size, 'metrics': metrics,
        'peak_allocated_mib': torch.cuda.max_memory_allocated() / 1024**2,
        'wall_seconds': time.monotonic() - started,
        'checkpoint_roundtrip': 'passed',
    }
    del agent, restored, module, other, before, a, b
    gc.collect()
report['passed'] = True
(ROOT / 'logs/server/accelerator-validation.json').write_text(json.dumps(report, indent=2))
print(json.dumps(report, indent=2))
