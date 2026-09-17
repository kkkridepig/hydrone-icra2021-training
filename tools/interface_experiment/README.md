# Hydrone PPU 两阶段界面机制实验

本入口基于仓库提交 8790a9ea1598c779e38e1fa5eeb3eeec6ca87bcf，适配已记录的阿里云 DSW、Ubuntu 20.04、ROS Noetic、Python 3.8 和 PPU-ZW810E 环境。它只增加 tools/interface_experiment 目录，保留原 DDPG/SAC、Lee、UUV、RotorS 源码与运行入口。

这是可运行的初步实验实现，不是已在 PPU/Gazebo 验证成功的算法。服务器实测结果由两个阶段生成，负结果与失败日志也会保留。请先阅读 LOCAL_VALIDATION.md 中的验证范围。

**整个实验只有两个阶段。**

| 阶段 | 一次命令内完成的工作 | 输出与继续条件 |
|---|---|---|
| phase1 | PPU 新网络算子探测；相机与控制链检查；先做两次干净双向跨越；通过后采集四种条件下的教师轨迹 | REPORT.md、gate.json、原始图像/轨迹、evidence.zip。所有运行门槛通过才允许 phase2 |
| phase2 | PPU 新网络算子检查；监督学习事件估计与 L1 动作块；独立场景中的五方法比较；生成配对统计 | 模型、训练曲线数据、完整执行日志、REPORT.md、report.json、evidence.zip |

默认 phase1 共 48 回合，phase2 共 120 回合；前两次干净跨越失败会提前停止，不会盲目完成所有回合。每回合最多 65 仿真秒，加 3 秒复位稳定过程。RTF=1 时最大仿真时间约 3.2 小时，另加启动、存储、训练与进程清理；实际用时以日志为准。默认一个模型随机种子、三个独立测试场景种子，用于机制试验，不构成论文级统计证据。

**代码获取后沿用现有环境，不安装或升级 torch、torchvision、CUDA、驱动。**

本次 GitHub 连接没有仓库写入权限，远端没有创建实验分支。下载 hydrone-interface-pilot-v1.zip 并上传到 /mnt/workspace，在已有服务器仓库应用包中的新增文件补丁。若有本地修改，先检查 git status，保留它们；不要使用 reset --hard。补丁只新增实验目录，git apply --check 会检查冲突。

    cd /mnt/workspace
    unzip hydrone-interface-pilot-v1.zip -d hydrone-interface-pilot-v1
    cd /mnt/workspace/hydrone_ws
    git status --short
    git apply --check /mnt/workspace/hydrone-interface-pilot-v1/hydrone-interface-pilot-v1.patch
    git apply /mnt/workspace/hydrone-interface-pilot-v1/hydrone-interface-pilot-v1.patch
    export HYDRONE_ROOT=/mnt/workspace/hydrone_ws
    export HYDRONE_BACKEND=ppu
    source tools/server/setup.bash

这些新增 Python/launch/xacro 文件直接使用既有已编译插件，不需要重新编译 catkin。补丁已经应用时不要重复应用，也不要覆盖已存在的 tools/interface_experiment。部署基线是本文首行的提交；若服务器底层代码后来有变化，请在解读报告时核对 provenance.json 的版本与文件哈希。不要在运行阶段一与阶段二之间变更实验源码、配置或底层模型；程序会比较源码与二进制哈希。

可以先执行无 Gazebo 的代码检查。这不是额外实验阶段，不生成假设结论：

    python -m unittest discover -s tools/interface_experiment/tests -v

测试中的合成数组只用于验证索引、因果时间对齐、网络更新和保存恢复，不能用作实验成功数据。正式入口没有合成环境回退，也不会在 PPU 不可用时静默切换 CPU 训练。

**第一阶段一次运行相机、控制与采集。**

    cd /mnt/workspace/hydrone_ws
    export HYDRONE_BACKEND=ppu
    mkdir -p logs/interface
    nohup bash tools/interface_experiment/run.bash phase1 \
      --output /mnt/workspace/hydrone_ws/artifacts/interface_phase1_v1 \
      > logs/interface/phase1_v1.log 2>&1 &

查看启动信息与逐回合进度：

    tail -f logs/interface/phase1_v1.log

    tail -f artifacts/interface_phase1_v1/worker.log

phase1 完成后检查：

    cat artifacts/interface_phase1_v1/REPORT.md
    cat artifacts/interface_phase1_v1/gate.json

只有诊断为 READY_FOR_PHASE2 才继续。仅有退出码 0 或启动 Gazebo 成功不等于研究假设成立。文件名中的 v1 是运行目录名，不是自动覆盖模式；同一路径已经存在时程序拒绝覆盖。重跑请改用 v2 等新路径，保留失败证据。

本阶段启动独立的 ROS master 11331 与 Gazebo transport 11361，不占用原训练默认的 ROS 11311。端口已使用会拒绝启动，可统一加 --port 11431 改用 11431/11461。程序只清理自己启动的进程组，不杀死已有 Python/ROS 进程。即使端口隔离，同时运行旧训练仍会争抢 CPU/PPU，影响时间测量；做正式比较时应避免资源竞争。

新增相机通过 Gazebo RGB 传感器工作，使用软件 OpenGL；无 DISPLAY 时自动调用 Xvfb。原激光训练不需要相机，因此这部分属于新增的服务器运行检查。如果报告明确指出缺少系统组件，可补齐以下系统包，然后重新运行新目录：

    apt-get update
    apt-get install -y ros-noetic-gazebo-plugins xvfb xauth libgl1-mesa-dri

需要管理员权限时使用相应管理员终端或 sudo。这些命令不修改 PPU 的 PyTorch。不要为解决相机渲染而安装 NVIDIA 驱动。

**第二阶段一次完成训练与配对测试。**

    cd /mnt/workspace/hydrone_ws
    export HYDRONE_BACKEND=ppu
    nohup bash tools/interface_experiment/run.bash phase2 \
      --data /mnt/workspace/hydrone_ws/artifacts/interface_phase1_v1 \
      --output /mnt/workspace/hydrone_ws/artifacts/interface_phase2_v1 \
      > logs/interface/phase2_v1.log 2>&1 &

查看训练：

    tail -f logs/interface/phase2_v1.log

训练完成后，逐回合测试写入：

    tail -f artifacts/interface_phase2_v1/evaluation/worker.log

最终报告：

    cat artifacts/interface_phase2_v1/REPORT.md

默认训练设备 cuda:0 是 PPU 镜像的兼容接口；小网络部署推理统一使用 CPU，并记录实际时延。这样可以避免把 PPU 调用开销当成算法差异。若手动使用 --device cpu，报告会明确记为 CPU 训练，不能声称 PPU 更新已验证。代码没有 AMP、FlashAttention、torch.compile、torchvision 或外部权重下载。

**四种条件通过不同渠道施加。**

| 条件 | 图像渠道 | 仿真动力学渠道 |
|---|---|---|
| clean | 原相机图像 | 原阻尼系数 |
| visual | 接近界面时加入受控混合、噪声和局部遮挡 | 原阻尼系数 |
| dynamics | 原相机图像 | 通过 /模型名/set_damping_scaling 修改 UUV 阻尼，并读回核对 |
| both | 图像扰动 | 阻尼改变 |

扰动由模拟器端真值决定，标签只供监督和报告，绝不进入网络特征。条件、场景种子、真实高度、注入系数与未来状态均不进入学生输入。图像与运动扰动的中心可存在偏移，场景及扰动强度按预定种子生成。动力学改变后轨迹和未来图像会变化，因此这是匹配初始条件的对照，不是四个完全相同的未来轨迹。

模型监督目标是图像估计高度、当前图像扰动状态、上一已完成执行区间的额外阻尼状态。运动头是因果历史上的反应式检测，不是尚未发生的扰动预知，也不是严格的真实流体辨识。图像扰动不等于物理折射/飞溅建模。

**学生观测与教师权限明确分开。**

- 教师使用真实位姿/速度进行有限幅度目标跟踪，只用于采集与可行性参考。
- 学生使用真实 RGB 加带噪声的水平位置、姿态、速度代理，以及 IMU 角速度/加速度、已发送命令与时间间隔；没有绝对 z 输入。
- 带噪声的里程计代理来自仿真真值加工，不是真实 VIO。所有学习方法使用同一代理传感假设，结果不能外推成已解决真实水下定位。
- 高度来自图像网络，再与垂直速度积分融合。相机固定安装于机体；水面固定在 z=0。没有证明可泛化到未知水面或波浪。
- 水面纹理若不能提供有效高度信息，模型可能失败，这属于实验结果，不使用真实高度替代学生输入来“修复”。

动作是三个物理参考：[机体前向速度、竖直速度、原桥接航向命令]，范围分别为 [0,.25]、[-.25,.25]、[-.25,.25]。物理 [0,0,0] 表示零速度参考。默认 legacy 模式保持原桥接的 yaw+command 旋转及 yaw-rate 同时传递语义；不能把第三维宣传为已经修正的标准接口。可选 yaw_rate 模式是显式的另一个接口配置，必须从 phase1 重新验证，不能混入同一次比较。

桥接保持约 50 Hz 仿真时间发布，状态反馈仍由 Lee 处理。高层目标周期 0.2 秒，预测 H=4，每行代表一个命令区间。执行器逐行消费动作块，过期后重规划，绝不把首行重复四次冒充动作块。日志包含实际时间间隔、命令入队时间、最近桥接发布及 watchdog；这些命令不是实测电机力矩。

**五种方法共用同一环境，四种学生共用同一模型权重。**

| 名称 | 定义 |
|---|---|
| teacher | 状态真值教师，检查可行性；不是同信息公平学习基线 |
| short | 每个高层时刻重新查询动作专家 |
| long | 每次消费完整四行动作块 |
| unified | 将两类事件分数的最大值用于观测降权及动作重规划 |
| separated | 图像分数用于观测降权，运动分数用于动作重规划 |

unified 和 separated 使用相同几何近界面触发规则、风险阈值、编码器、动作专家和观测信息，因此这是对“是否区分原因来使用判断结果”的受控消融。它不是对 BCP、DEHP、FFDC、πR² 或全部六篇基线的完整复现。所有学生每个高层时刻运行图像编码和事件估计，当前版本只可能节约动作专家查询，不声称已经节省视觉计算。应看完整 decision_ms，而不是仅看专家调用次数。

事件估计使用小 CNN 和固定长度历史 MLP，动作专家是小 MLP 输出 H×3、用 L1 监督。这里没有声称实现完整 ACT Transformer、DINOv3 或新的生成算法。这些简化用于先验证控制机制。

训练场景种子 0–3；验证 10、11；测试 100–102。种子及其全部方向/条件归属同一集合，不能按帧随机拆分。模型选择只使用验证集。动作专家只使用成功教师轨迹；失败轨迹保留并用于事件学习与失败分析。尾部动作块采用 mask，不跨回合拼接。动作专家的训练输入使用已训练估计器输出，不把真实高度偷偷当部署输入。

**第一阶段通过只代表运行与控制可行性，不等于物理模型已校准。**

原仓库 Fossen 水动力的空水作用范围、部分浸没浮力分段、固定电机系数都保留。程序导出 expanded_robot.urdf 与 physics_inventory.json，包括质量、排水体积和理论浮力，用于后续审查。没有根据预期结论擅自调大浮力或更换电机模型。

如果干净条件下三维参考命令＋Lee 无法完成跨越，phase1 停止并保留报告。应先根据轨迹判断控制权限、模型和接口，不放宽门槛掩盖失败。若未来修正底层模型，要将其作为新配置和新数据运行，而不能复用旧报告宣称同一实验。

**回传两个 evidence.zip 即可开始分析。**

    /mnt/workspace/hydrone_ws/artifacts/interface_phase1_v1/evidence.zip
    /mnt/workspace/hydrone_ws/artifacts/interface_phase2_v1/evidence.zip

若 phase1 未通过，只提供第一个压缩包即可。包内包含报告、配置、源码与数据哈希、真实逐步/逐回合日志、相机预览、训练指标和错误说明；不包含体积较大的 npz 原始训练数组与 pt 权重。后两者继续保留在服务器，必要时按具体案例补传。

成功穿过水面不等于成功：任务还要求到达另一介质目标、低速与低姿态误差持续一段时间。失败区分高度边界、水平边界、激光接近、姿态、角速度、超时和基础设施错误。报告同时统计未跨越、跨越率、完整任务成功、姿态/角速度峰值、时延与动作查询。配对区间按测试场景种子聚类，仅供探索性分析。

研究判断重点是 separated 是否在相同模型与传感信息下优于 unified，并且优势没有完全被固定 short 的高频反馈解释。若没有改善，报告仍是有效负结果，不应为得到提升而反复调测试集。
