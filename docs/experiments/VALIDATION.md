# 迁移与离线验证记录

日期：2026-09-17。本次实际执行环境为 Windows、Python 3.12.0、NumPy 2.5.2、Git for Windows 2.53.0。绘图使用仓库外临时 venv 中的 matplotlib 3.11.2。未安装 torch，未连接或更改 PPU 服务器，未启动 Gazebo 训练。

## 实际执行结果

| 检查 | 结果与边界 |
|---|---|
| Git 基线/状态 | 隔离新 clone 初始干净；main=`8790a9ea1598c779e38e1fa5eeb3eeec6ca87bcf`，与旧基线相同；未发现仓库 AGENTS.md |
| 输入目录安全 | 8 个目录、1382 个文件；无符号链接/路径越界；没有原始 ZIP，不能声称检查过原 ZIP 成员 |
| 包 manifest | 初始 14 源文件＋patch，clockfix 3＋patch，diagnostic 9＋patch 全部 SHA256 匹配 |
| 补丁检查 | 三个原 patch 分别先 `git apply --check` 成功，再应用一次；应用结果与完整文件逐字节一致 |
| 初始包原测试 | `python -m unittest discover -s tools/interface_experiment/tests -v`：20 项发现，18 通过、2 跳过 |
| clockfix 后原测试 | 同命令：33 项发现，31 通过、2 跳过；其中新增 `test_startup.py` 的 13 项全部通过 |
| diagnostic 原测试 | `python -m unittest discover -s tools/interface_diagnostic/tests -v`：17 项通过、0 跳过 |
| 最终去重测试数 | 两个最终套件共 50 项：48 通过、2 跳过；不能把重复执行的原 18 项重复计数 |
| Python3.8 语法 | 16 个 Python 文件通过 Python3.12 的 `ast.parse(..., feature_version=(3, 8))`；**不是 Python3.8 运行验证** |
| bash 语法 | Git Bash `bash -n` 检查两个实验 `run.bash` 与主文档中的 10 个 bash 代码块，通过；没有 source 服务器环境 |
| CLI 帮助 | 实验 run.py、diagnose.py、audit.py 的 `--help` 均退出 0 |
| 诊断启动失败路径 | 真入口指向不存在的 phase2，退出 2、状态 INCOMPLETE；错误/报告/evidence 均保留，未启动仿真 |
| 输出防覆盖 | 同一失败输出目录再次运行被拒绝，原 report.json hash 不变 |
| 原 phase1 报告 | 在副本上用原 report.py 重算，48/48，gate 与原记录完全相同 |
| 首次 phase2 报告 | 在副本上重算，0/120、INCOMPLETE，与原记录一致；不是教师 0% 的实测结果 |
| 成功 phase2 报告 | `第三次_evidence` 副本重算 120/120、各方法 24/24、PILOT_RESULTS_REQUIRE_REVIEW |
| phase1 数据身份 | 对实际存在的 192 项逐文件核验通过；原 manifest 另列 48 个未提供的 NPZ；phase2 的 240 项 collection_sha256 与原 manifest 一致 |
| clockfix 迁移 | 用原 compatibility 函数核验两份历史 source_sha256，返回值与成功 phase2 的迁移记录完全一致 |
| 源码身份 | 成功 phase2 与 diagnostic 的 source_sha256 一致，336 项中本地存在的 296 项全部匹配；另外 40 项为 devel 构建产物，未本地验证 |
| diagnostic 源码 | 8 项受监控源码 hash 与真实 diagnostic provenance 一致；包含 README 的 9 个交付文件另与包 manifest 一致 |
| phase2→diagnostic 引用 | 成功 phase2 report/provenance 的字节 hash 与 diagnostic 所记录的两个来源 hash 完全一致 |
| 原 audit.py 重算 | 36 校准＋168 测试；612 个 manifested 文件全部匹配；204 个 NPZ 按原规则省略；204 回合 steps 均匹配 |

`test_learning.py` 中的 `test_actual_gradient_update` 与 `test_short_fit_and_checkpoint_roundtrip` 因本机没有 torch 而跳过。没有为了增加通过数安装普通 torch；应在既有服务器 vendor 环境补跑。

以上单元测试的模拟 ROS/FixtureEnv/合成数组只检查软件契约，不属于任何 Gazebo 实验成功证据。48/48、120/120 和诊断数值来自用户提供的真实逐回合文件，来源没有混用。

## 审计重算的具体身份

原 `audit.py` 原样执行，输入是将收到的 diagnostic evidence 目录按原相对路径打包的新 ZIP。打包前检查路径与链接，打包后检查 626 个成员、重复路径和 CRC。它是临时适配容器，不提交 Git、不声称是上传原件。

```text
历史审计引用的原 ZIP SHA256（仅引用）：
ac537c9f5c489d93ea5a601796a73f5126136ddf6aa88a217d709a5b473ba21e

本次重打包 SHA256（本地实算）：
9e967c9fd2077c3cd7787dfec569d88295be302e44cf4f2c0c6a34d20c9d5b37
```

实际命令等价于：

```bash
python tools/interface_analysis/audit.py diagnostic-evidence-repacked.zip new-audit-output
```

结果见 [audit-recomputed.json](2026-09-17/audit-recomputed.json)。与 [原 audit.json](2026-09-17/audit-original/audit.json) 比较，差异仅为 `input_sha256` 和三个距离值约 `2.8e-17` 的浮点尾差；方法计数、查询数、事件头分母/计数、选择档位与失败分类全部一致。原图被保留，重算也成功生成图；不要求不同 matplotlib/NumPy 环境生成的 PNG 字节一致。

重算确认：teacher/short 各 24/24；long/geometry/unified/separated/oracle 各 20/24；168 回合全部 crossed；20 次任务失败全部为 seed201 空入水的 height_boundary。专家查询依次为 short2303、long660、geometry1117、unified1441、separated1403、oracle1127。separated 运动注入窗口为 4/49，clean 报警 214/650。含义和局限见 [研究状态](RESEARCH_STATUS_20260917.md)。

原脚本的 612 项 hash 校验覆盖 manifest 列出的回合文件；其余顶层配置/日志的本次身份在 input-inventory.json 中记录，不把新生成 manifest 当成独立的历史签名。

## 提交前检查与处理

三轮有实质变化的检查分别对应初始包、clockfix 与 diagnostic 集成。源码均保持原件字节，没有为通过测试重写 provenance、删除身份检查、改变奖励/阈值/物理/动作语义或自动格式化。

新增两份派生 JSON 首次 `git diff --check` 因 Windows CRLF 报尾部空白；只将本次生成的 `audit-recomputed.json` 与 `input-inventory.json` 保存为 LF，再检查通过。原 Python/JSON/launch、原历史报告及包 manifest 没有换行转换。

最终检查包含 `git diff --check`、提交范围/大文件/凭据模式检查、文档相对链接核验，以及提交对象和 checkout 的原件 SHA256 对照。源目录 `src/`、`tools/server/` 和既有 provenance 未修改；没有纳入 venv、构建产物、模型、训练数组、长日志或重复 ZIP。详细机器可读结果见 [validation-results.json](2026-09-17/validation-results.json)。

## 未执行或待补齐

- **Python3.8 真实运行、ROS/Gazebo、PPU 算子/梯度、相机/施力/时钟服务时序、完整闭环重跑**：当前是 Windows 离线环境，且本任务未授权自动启动长时训练。部署后的服务器验证按 [DEPLOYMENT.md](DEPLOYMENT.md) 执行。
- **40 项原构建产物、系统 Gazebo 插件**：只保留历史记录，没有本地字节；不可宣称新构建已兼容原模型/数据。
- **原 model.pt 的独立 hash 与模型推理**：权重未提供。历史 hash `83f8c9646a29d8ade96ac7a327b6923694eace9767fda0a769b0e16a4e5728ea` 来自 diagnostic provenance；应在原服务器核验。
- **训练/诊断 NPZ 原字节**：evidence 按原设计省略，不能重训或审核全部相机数组。原清单哈希一致不代表本地读取过缺失数组。
- **四个交付包与四组 evidence 的原 ZIP 容器**：未提供，包 SHA256、原 ZIP 元数据/成员安全性仍未知。已完成解压内容的核验与迁移，不声明原 ZIP 链条完整。
- **56 回合、真值/学习高度对照、到达后继续 10 秒**：仅提案，没有代码入口、运行或通过记录。

本次联网读取 ROS Noetic `simtime.py` 确认初始化时依据 `/use_sim_time` 选择时钟的代码背景；[研究状态](RESEARCH_STATUS_20260917.md) 所列五篇近邻的 arXiv 题名/摘要也重新读取。它们不是服务器验证的替代。
