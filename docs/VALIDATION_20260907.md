# 2026-09-07 发布复查

验证范围是当前 `icra2021_paper.launch` + 独立 DDPG/SAC 实现，未逐个验证全部历史脚本。论文指标/收敛仍需正式实验验证。

本机：Ubuntu 20.04、ROS Noetic、Gazebo Classic 11、Python 3.8.20、torch 2.4.1+cu121、RTX 4060 Laptop。阿里云新 PPU 镜像未直接连接，版本及可运行性必须由服务器探测确认，不能以本机 CUDA 成功代替 PPU 成功。

本轮修改与检查：

- 修复 ROS 关闭时的传感器等待异常分类：关闭转换为 runner 能识别的 RuntimeError；运行期间的真正 ROS 数据超时仍为 TimeoutError。增加两种情况的回归测试。旧正式运行日志在末尾同时存在 shutdown 和 timeout，不能仅凭该日志断言硬件或训练算法故障。
- 41 项自动测试通过，包含模型、动作/观测、目标采样、终止/截断、checkpoint、训练/评估/恢复及异常状态。发布快照也通过同样 41 项测试。
- 相关 ROS 包 `hydrone_aerial_underwater_deep_rl` 通过 catkin_tools 构建；沿用既有 41 包工作区。本轮未在第二个工作区或 PPU 服务器重新全量构建。
- 通用加速器脚本在本机验证 DDPG/SAC 的 FP32 梯度更新、参数变化和 checkpoint 恢复，均为 hidden_dim=512、batch=256、各 5 次更新。
- 新服务器启动器在本机真实 Gazebo 中验收，使用独立输出目录、防覆盖、明确结果状态和 checkpoint 数值检查。首次开发测试发现解析后配置仍带相对 base_config，已从生成的完整配置中移除并重测。
- Stage 2 运行时确认四个静态障碍物在 (2,2,2)、(-2,-2,2)、(2,-2,2)、(-2,2,2)；3 秒内收到 15 帧、每帧 20 束 LaserScan，约 100 Hz 两路 odometry，约 50 Hz trajectory；cmd_vel 只有 /icra2021_agent 发布。

完整顺序测试表和机器可读证据在发布完成时附于 `provenance/validation-20260907.json`。配置、代码 SHA256 和完整本机日志保留在本机工作区 `artifacts/server` 与 `logs/environment/server-gates-review.log`；Git 仓库不携带测试 checkpoint。

服务器部署脚本已做语法检查，并在 NVIDIA 本机检验公共启动与验证流程。PPU 环境创建和 ROS 安装脚本没有在目标 DSW 实例执行。PPU 镜像必须保留厂商 torch、驱动和库路径，先验证 `probe_ppu.py`，再创建继承系统包的 venv，最后经过模型更新、1/100/1000 步与短训练关卡。

完整训练须显式 `--full`。本轮没有启动新的正式长训练。四个算法/阶段从头独立训练，不隐式跨阶段恢复。checkpoint 保存期间异常断电仍可能损坏当前文件，重要实验应定期备份已经完整写完的 checkpoint 到独立路径。

## 本轮完整仿真结果

| 算法 | Stage | 1/100/1000 步 | 1000 步梯度更新 | 1000 步耗时 | 两回合短训练步数 |
|---|---|---|---|---|---|
| DDPG | 1 | 全部通过 | 745 | 202.99 秒 | 589 |
| SAC | 1 | 全部通过 | 745 | 201.26 秒 | 1000 |
| DDPG | 2 | 全部通过 | 745 | 202.51 秒 | 506 |
| SAC | 2 | 全部通过 | 745 | 202.16 秒 | 833 |

16 次顺序仿真全部通过。每项 checkpoint 的已完成回合数和全局步数均与 summary 对齐；1000 步均发生实际梯度更新。

额外 SAC Stage 2 单步恢复通过：梯度更新从 745 到 746，原始 checkpoint SHA256 保持不变，恢复后的文件保存在独立实验目录。
