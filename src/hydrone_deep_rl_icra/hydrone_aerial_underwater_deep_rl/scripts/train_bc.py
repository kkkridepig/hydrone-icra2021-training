#!/usr/bin/env python3

import argparse
import pandas as pd
import torch
from torch.utils.data import DataLoader,TensorDataset
from bc_policy import BCPolicy


parser = argparse.ArgumentParser()
parser.add_argument("--data",default="hydrone_bc_dataset.csv")
parser.add_argument("--epochs",type=int,default=200)
parser.add_argument("--save",default="bc_policy.pth")
args=parser.parse_args()


df=pd.read_csv(args.data)

state_cols=[
"x","y","z",
"vx","vy","vz",
"gx","gy","gz",
"submerged"
]

action_cols=[
"cmd_vx",
"cmd_vy",
"cmd_vz",
"cmd_yaw"
]


X=torch.tensor(
    df[state_cols].values,
    dtype=torch.float32
)

Y=torch.tensor(
    df[action_cols].values,
    dtype=torch.float32
)


loader=DataLoader(
    TensorDataset(X,Y),
    batch_size=256,
    shuffle=True
)


model=BCPolicy()

opt=torch.optim.Adam(
    model.parameters(),
    lr=1e-3
)

loss_fn=torch.nn.MSELoss()


for e in range(args.epochs):

    total=0

    for x,y in loader:

        pred=model(x)
        loss=loss_fn(pred,y)

        opt.zero_grad()
        loss.backward()
        opt.step()

        total+=loss.item()

    if e%20==0:
        print(
            "epoch",
            e,
            "loss",
            total/len(loader)
        )


torch.save(
    model.state_dict(),
    args.save
)

print("saved:",args.save)
