#!/usr/bin/env python3
import torch
import torch.nn as nn


class BCPolicy(nn.Module):
    def __init__(self, input_dim=10, action_dim=4):
        super().__init__()

        self.net = nn.Sequential(
            nn.Linear(input_dim,128),
            nn.ReLU(),
            nn.Linear(128,128),
            nn.ReLU(),
            nn.Linear(128,64),
            nn.ReLU(),
            nn.Linear(64,action_dim)
        )

    def forward(self,x):
        return self.net(x)
