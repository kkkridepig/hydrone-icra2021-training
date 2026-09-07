#!/usr/bin/env python3
"""Run with the image's original Python, before installing anything."""
import json
import platform
import sys
import torch

report = {'python': sys.version, 'executable': sys.executable,
          'os': platform.platform(), 'torch': torch.__version__,
          'torch_path': torch.__file__, 'cuda_interface': torch.version.cuda,
          'available': torch.cuda.is_available(), 'devices': []}
if report['available']:
    for i in range(torch.cuda.device_count()):
        report['devices'].append({'name': torch.cuda.get_device_name(i),
                                  'memory_gib': torch.cuda.get_device_properties(i).total_memory / 1024**3})
    layer = torch.nn.Linear(26, 3).to('cuda')
    optimizer = torch.optim.Adam(layer.parameters(), lr=0.001)
    loss = layer(torch.ones(4, 26, device='cuda')).square().mean()
    loss.backward()
    optimizer.step()
    torch.cuda.synchronize()
    report['fp32_adam_passed'] = bool(torch.isfinite(loss).item())
print(json.dumps(report, indent=2))
assert sys.version_info[:2] == (3, 8), 'This Hydrone ROS Noetic setup requires Python 3.8'
assert report['available'] and report['fp32_adam_passed'], 'Accelerator probe failed'
assert any('PPU' in d['name'].upper() for d in report['devices']), 'Expected PPU image'
