# LogFormer — 微服务系统日志异常检测

![Python](https://img.shields.io/badge/python-3.10+-blue)
![PyTorch](https://img.shields.io/badge/PyTorch-2.0+-orange)

LogFormer（AAAI 2024）论文复现项目，在 **Online Boutique** 微服务演示系统（10 个服务，Kubernetes 部署）上实现日志异常检测的完整流水线。

**核心结果**：BGL（超级计算机日志）→ Online Boutique 跨域迁移，**F1=0.81**，仅训练 **10% 的参数量**即达到全量微调的效果。

---

## 什么是 LogFormer？

实际问题：不同系统的日志格式千差万别，在一个系统上训练的异常检测模型换到另一个系统效果就很差。每个系统都从头训练成本太高。

**解决方案**：两阶段训练框架
1. **预训练阶段**：在大型标注日志数据集（如 BGL 超级计算机日志，470 万行）上训练 Transformer 编码器，让它学会理解日志的通用语义模式
2. **微调阶段**：冻结预训练好的主干网络，在每层 Transformer 中插入轻量级 **Adapter** 模块，只更新约 10% 的参数即可适配新系统

这样可以在公开数据集上预训练一次，然后快速适配到任何目标系统。

---

## 项目结构

```
├── LogFormer/                      # 核心实现
│   ├── model.py                    # Transformer + Adapter 模型架构
│   ├── dataloader.py               # PyTorch 数据加载器
│   ├── train_transformer.py        # 训练脚本
│   ├── tune_transformer.py         # Adapter 微调脚本
│   ├── Drain.py                    # 日志解析算法（前缀树）
│   ├── preprocess/
│   │   ├── preprocess_json_logs.py # Online Boutique JSON → .npz 预处理流水线
│   │   └── preprocess_bgl.py       # BGL → .npz 预处理流水线
│   └── 实验报告.md                  # 完整实验记录与分析
├── LogFormerDataCollector.py       # Kubernetes 日志采集工具 (kubectl logs -f)
├── log_data/json_logs/             # Online Boutique 原始 JSON 日志
├── BGL/                            # BGL 数据集（含 2000 行样本）
├── OnlineBoutique_data/            # Prometheus 指标数据 + 混沌工程配置
├── LogADEmpirical/                 # 基准对比框架（ICSE'22）
└── .gitignore
```

---

## 数据预处理流水线

```
原始 JSON 日志（10 个微服务，198K 行）
    │
    ▼ 第 1 步：消息提取
    处理 5 种日志格式（Go JSON、Java log4j、Python、Node.js、C# .NET），提取纯文本消息
    │
    ▼ 第 2 步：Drain 日志解析
    198K 条消息 → 322 个唯一模板（如 "received ad request <*>"）
    │
    ▼ 第 3 步：向量化编码
    每个模板 → 384 维向量（paraphrase-MiniLM-L6-v2）
    │
    ▼ 第 4 步：窗口化
    每 20 条连续日志打包成一个样本 → (20, 384) 矩阵
    │
    ▼ 第 5 步：异常标注（精准行级标注）
    检测 "severity":"error" / "http.resp.status":500 等字段
    窗口内 ≥30% 行异常 → 整个窗口标为异常
    │
    ▼ 输出：.npz 文件
    preprocessed_data/OnlineBoutique_{training,testing}.npz
```

### 数据集概况

| 数据 | 行数 | 窗口数 | 异常占比 |
|------|------|--------|---------|
| 正常（10 个服务，30 分钟） | 148,886 | — | 0% |
| 故障（frontend，5 种故障类型） | 49,566 | — | 约 44% 行 |
| **合计** | **198,452** | **9,940** | **24.9%** |

---

## 模型架构

```
输入: (batch, 20, 384) — 每个窗口 20 条日志嵌入
    │
    ▼ 位置编码（PositionalEncoding）
    │
    ▼ TransformerEncoder（4 层，每层 8 个注意力头）
    │   ┌─────────────────────────────────────┐
    │   │ Self-Attention → Adapter → Add+Norm  │  ← 每层注入 Adapter
    │   │ FFN → Adapter → Add+Norm             │
    │   └─────────────────────────────────────┘
    │
    ▼ 均值池化（Mean Pooling）→ 384 维
    │
    ▼ Linear(384, 2) → [正常分数, 异常分数]
```

### 与论文的主要差异

| 项目 | 原论文 | 本实现 | 原因 |
|------|--------|-------|------|
| 嵌入维度 | 768 | **384** | 使用 paraphrase-MiniLM-L6-v2 |
| Transformer 层数 | 1（默认） | **4** | 增加模型容量 |
| 分类头 | 展平 + Linear(7680, 2) | **Mean Pooling** + Linear(384, 2) | 参数量减少 95%，防止过拟合 |
| Loss 权重 | 均匀权重 | **异常权重 1.5** | 正负样本不均衡（约 1:3） |
| 学习率策略 | — | **OneCycleLR** | 先升温后衰减 |

### Adapter 模块

```
Linear(in_dim, hidden_dim) → GELU → Linear(hidden_dim, out_dim)
                     残差连接: 输出 + 输入
```

微调时，只训练 Adapter + LayerNorm + 分类头，约占总参数量的 10%。

---

## 实验结果

### BGL 预训练

| Epoch | 精确率 | 召回率 | F1 |
|-------|--------|--------|-----|
| 4 | 93.6% | 97.5% | **0.96** |

### Online Boutique 直接训练

| Epoch | 精确率 | 召回率 | F1 |
|-------|--------|--------|-----|
| **2** | **68.5%** | **99.8%** | **0.81** |

### 跨域迁移（BGL → Online Boutique）

| 方法 | F1 | 精确率 | 召回率 | 训练参数量 |
|------|-----|-------|--------|-----------|
| 全量微调 | **0.81** | 68.5% | 99.8% | 100%（约 200K） |
| Adapter (64) | 0.71 | 57.6% | 91.4% | 约 5% |
| **Adapter (128)** | **0.81** | 68.2% | 98.3% | **约 10%（约 20K）** |

### 消融实验：F1 逐步提升过程

| 步骤 | 改动 | F1 |
|------|------|-----|
| 1 | 单故障 + 原始架构 | 0.66 |
| 2 | + 只保留 frontend 日志 | 0.70 |
| 3 | + 按行精确标注 error | 0.74 |
| 4 | + 5 种故障类型 | 0.74 |
| 5 | **+ Mean Pooling + 4 层 Transformer** | **0.81** |
| 6 | + BGL 预训练 + Adapter(128) | 0.81 |
| 7 | + 30% 窗口纯度阈值 | 0.80 |

---

## 快速开始

### 环境要求

- Python 3.10+
- PyTorch 2.0+
- sentence-transformers
- CUDA（推荐）

### 安装依赖

```bash
pip install torch numpy pandas scikit-learn sentence-transformers tqdm
```

### 在 Online Boutique 数据上训练

```bash
cd LogFormer

# 第 1 步：预处理 JSON 日志 → .npz
python preprocess/preprocess_json_logs.py

# 第 2 步：训练模型
python train_transformer.py --log_name OnlineBoutique --window_size 20 --num_layers 4
```

### 跨域迁移（BGL → Online Boutique）

```bash
cd LogFormer

# 第 1 步：预处理 BGL 数据
python preprocess/preprocess_bgl.py

# 第 2 步：在 BGL 上预训练
python train_transformer.py --log_name BGL --window_size 20 --num_layers 4

# 第 3 步：Adapter 微调到 Online Boutique
python tune_transformer.py \
  --pretrained_log_name BGL \
  --log_name OnlineBoutique \
  --load_path checkpoints/train_BGL_classifier_4_64_1e-05-best.pt \
  --tune_mode adapter \
  --window_size 20 \
  --num_layers 4 \
  --adapter_size 128 \
  --epoch 20
```

### 从 K8s 采集自己的日志

```bash
# 采集正常日志（例如 5 分钟）
python LogFormerDataCollector.py --duration 300 --phase normal

# 采集 frontend 故障日志
python LogFormerDataCollector.py --duration 300 --phase fault --service frontend
```

---

## 数据说明

### Online Boutique 日志（已包含在仓库中，约 54MB）
- `log_data/json_logs/*_normal.json` — 10 个服务的正常日志（30 分钟）
- `log_data/json_logs/frontend_fault.json` — frontend 故障日志（5 种故障类型）

### BGL 数据集（需单独下载，约 709MB）
- 从 [USENIX CFDR](https://www.usenix.org/cfdr-data#hpc4) 下载
- 将 `BGL.log` 放入 `BGL/` 目录
- `BGL/BGL_2k.log` 已包含 2000 行样本供测试

---

## 关键发现

1. **故障多样性比数据量更重要**：5 种不同类型的故障比单一故障数据效果更好
2. **标签精度决定上限**：按行精确标注比整文件弱标注提升 F1 约 0.04
3. **架构适配减少过拟合**：Mean Pooling 替代 Flatten 使参数量减少 95%，F1 提升 0.07
4. **Adapter 瓶颈必须足够大**：跨域迁移时需要 dim=128（10% 参数），dim=64（5% 参数）信息丢失严重

---

## 参考文献

- **LogFormer**: Zhang 等, "LogFormer: A Pre-train and Tuning Pipeline for Log Anomaly Detection", AAAI 2024
- **BGL 数据集**: Oliner & Stearley, "What Supercomputers Say: A Study of Five System Logs", DSN 2007
- **Drain**: He 等, "Drain: An Online Log Parsing Approach with Fixed Depth Tree", ICWS 2017
- **LogADEmpirical**: Zhu 等, "LogAI: A Unified Log Intelligence Benchmark", ICSE 2022
- **Online Boutique**: [GoogleCloudPlatform/microservices-demo](https://github.com/GoogleCloudPlatform/microservices-demo)

---

## 许可

本项目仅用于研究和教育目的。
