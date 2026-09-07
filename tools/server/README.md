# Ubuntu 20.04 / Python 3.8 服务器部署

本目录支持阿里云 PAI DSW PPU，以及本机 NVIDIA。默认使用 NVIDIA 路径；PPU 每次开启 Terminal 必须设置 `export HYDRONE_BACKEND=ppu`。

## PPU 必须保留镜像运行时

用户提供的旧实例记录为 PPU-ZW810E，96 GiB，厂商 PyTorch 2.4.0，Python 3.10，CUDA 兼容接口 12.3。**这些不是新镜像的实测版本**。用户当前新镜像为 Ubuntu 20.04 / Python 3.8 / cu121，PyTorch、驱动、PPU 数量尚待探测。

不能在 PPU 实例安装 NVIDIA torch wheel、NVIDIA 驱动或 CUDA Toolkit，也不能使用 `tools/environment/conda-gpu.yml`。采用 `venv --system-site-packages` 继承厂商 PyTorch；不更改全局 pip 源，也不清空厂商的 LD_LIBRARY_PATH。代码只使用 FP32 MLP 和 torch.cuda 接口；实际兼容性由下面的模型更新和 checkpoint 测试决定。

## 1. 克隆到持久化盘

在 DSW 网页 Terminal 使用自己有写权限的 CPFS 子目录。历史 `/mnt/workspace`、`/mnt/data` 可能是同一挂载，先 `df -hT /mnt/workspace` 确认；不要把代码唯一副本放临时根盘。下面使用历史项目父目录下新建的 hydrone_ws；若当前实例对该父目录没有写权限，改为自己的 CPFS 目录。不要复用旧 TurboVLA 目录。

```bash
mkdir -p /mnt/workspace/vads-vke/vlm/yjl
cd /mnt/workspace/vads-vke/vlm/yjl
git clone git@github.com:kkkridepig/hydrone-icra2021-training.git hydrone_ws
cd hydrone_ws
python tools/server/probe_ppu.py
ppu-smi
```

这是私有仓库：服务器需要自己的 GitHub SSH key 并授权访问，或使用 `gh auth login` 后 `gh repo clone kkkridepig/hydrone-icra2021-training hydrone_ws`。不要把 token 写进 URL 或提交到 Git。没有服务器 GitHub 访问时，可在本机下载源码归档后用 DSW 文件上传；需要保留 provenance 文件。

先保存 `probe_ppu.py` 输出。它检查 Python 3.8、实际 PPU、FP32 前向/反向及 Adam；失败时停止，不安装或替换 torch。

## 2. 安装 ROS 并编译

```bash
bash tools/server/install_ros.bash
bash tools/server/build.bash
```

安装脚本支持 DSW root 或有 sudo 的普通用户，只面向 Ubuntu 20.04。使用带签名的 TUNA ROS 源、补充 focal-updates 和项目依赖；APT 采用 `--no-remove`，遇到冲突立即停止。系统包依赖仍受镜像源可用性影响，不等同于完全冻结的系统镜像。重建 DSW 后系统包与编译结果应重新检查。

构建使用系统 `/usr/bin/python3` 和 catkin_tools，默认 2 个编译任务。没有复制本机 build/devel。若编译环境缺包，先保留完整错误，不切换 ROS 2/Gazebo 版本。

## 3. 创建 PPU Python 环境

从镜像原生 shell 执行，先退出旧项目 venv；`python` 必须是镜像自带且能导入 PPU torch 的 Python 3.8。

```bash
bash tools/server/create_ppu_env.bash
export HYDRONE_BACKEND=ppu
source tools/server/setup.bash
python tools/server/validate_accelerator.py
python -m pytest -q src/hydrone_deep_rl_icra/hydrone_aerial_underwater_deep_rl/tests/icra2021
```

若镜像 Python 路径不同，设置 `HYDRONE_PPU_PYTHON=/实际/python路径` 后创建。脚本固定必要的 ROS/Python 上层依赖，约束 torch/torchvision/torchaudio/numpy 为镜像现有版本，并验证安装前后 torch 路径和版本未变化。日志保存在 `logs/server`。新镜像变更后不要直接复用旧 venv。

`validate_accelerator.py` 使用真实 DDPG/SAC、512 隐层、batch 256、实际梯度更新以及 checkpoint 保存/恢复。它通过后才运行仿真。

## 4. 顺序验收仿真

```bash
export HYDRONE_BACKEND=ppu
bash tools/server/gates.bash > logs/server/gates.log 2>&1
```

顺序执行四个算法/阶段组合的 1、100、1000 步，再各做 2 回合短训练；任何一项失败立即停止。每次创建独立输出目录，检查 summary 完成状态、精确步数、checkpoint 有限值及梯度更新；成功打印 `ALL_SERVER_GATES_PASSED`。不会启动正式训练。

当前入口使用 Gazebo Classic CPU ray LaserScan，不依赖 RGB-D 摄像头或 NVIDIA 渲染。`gui=false` 不启动 gzclient。若服务器报告 X11/OGRE 渲染初始化失败，可对同一有界测试使用 `LIBGL_ALWAYS_SOFTWARE=1 xvfb-run -a bash tools/server/run.bash --algorithm ddpg --stage 1 --steps 1`；该服务器回退尚未实测，成功后再继续下一关。

查看运行：

```bash
tail -f logs/server/gates.log
```

每个 `OUTPUT=...` 目录内有 `launch.log`、`config.yaml`、`invocation.json`、`run`、checkpoint 及成功后的 `validation.json`。仅 roslaunch 返回 0 不代表训练成功。

## 5. 正式训练：四个独立实验

全部验收通过后，逐条执行下面四个命令；不要同时启动，共用 localhost ROS/Gazebo 端口。Stage 1 为 1000 回合，Stage 2 为 2500 回合，每回合最多 500 步；碰撞会提前终止，所以实际步数不是固定 50 万/125 万。

```bash
export HYDRONE_BACKEND=ppu
bash tools/server/run.bash --algorithm ddpg --stage 1 --seed 0 --full
bash tools/server/run.bash --algorithm sac  --stage 1 --seed 0 --full
bash tools/server/run.bash --algorithm ddpg --stage 2 --seed 0 --full
bash tools/server/run.bash --algorithm sac  --stage 2 --seed 0 --full
```

这四项从头独立训练。Stage 2 是另一场景，**不自动接着 Stage 1 训练**；跨阶段迁移不是当前已定义的论文契约。默认 5 Hz 传感器闭环，设备显存充足并不保证同比加速 Gazebo 采样。配置中的论文值及公开实现差异见 `src/hydrone_deep_rl_icra/REPRODUCTION_NOTES.md`；短测试成功不证明策略收敛或复现论文指标。

网页 Terminal 长训练可使用 `nohup`，例如只启动第一项：

```bash
export HYDRONE_BACKEND=ppu
nohup bash tools/server/run.bash --algorithm ddpg --stage 1 --seed 0 --full > logs/server/ddpg-stage1.log 2>&1 &
echo $!
```

下一项等上一项完成后再启动。`tail -f` 后 Ctrl+C 只停止查看日志。

## 6. 中断恢复

```bash
export HYDRONE_BACKEND=ppu
bash tools/server/run.bash --algorithm ddpg --stage 1 --seed 0 --full --resume /持久化盘/原实验/checkpoint.pt
```

只加载自己信任的 checkpoint。启动器先复制到新实验目录，保持原文件不变；检查同算法、同阶段，由 agent 进一步检查网络/观测/动作/训练配置。恢复优化器、replay、RNG 和已完成回合计数；Gazebo 世界状态没有被保存，因此不保证与不中断训练逐位等价。定期保存每 10 回合，退出时也尝试保存；硬杀进程/实例断电可能只能恢复上次完整 checkpoint。

每个输出目录自动防覆盖。切换种子或超参数应从头新建实验；不要拿 Stage 1 checkpoint 冒充 Stage 2 的 resume。

## NVIDIA 本机分支

本机验证环境：Python 3.8.20、torch 2.4.1+cu121、RTX 4060 Laptop。`tools/environment/conda-gpu.yml` 仅给 NVIDIA 分支使用；Conda 路径可通过 `HYDRONE_CONDA_ROOT` 设置。`HYDRONE_BACKEND` 不设或设 nvidia 时使用 Conda 环境 `hydrone-gpu`。PPU 与 NVIDIA 的结果必须分别记录。
