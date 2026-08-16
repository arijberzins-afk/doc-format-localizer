# 政府公文格式保留本地化工具

将外省政府公文（PDF/图片）的省份、机关等地域信息替换为目标省份对应信息，**完全保留原始版面、字体、颜色和排版**。

## 功能特点

- **格式完全保留** — 原版面、字体、字号、颜色、行距、边距完全不变
- **PDF 文字层精确替换** — 从 span 读取精确坐标、字号、颜色，redact 擦除后写回
- **智能字间距红头** — 自动识别字间距排版的红头（每字单独成行），整行居中重排
- **LLM 分批词表生成** — 每3页一批调用 LLM，只输出省市地名（不带机构后缀），避免误替换
- **正则兜底** — 发文字号前缀自动转换（如 `粤` → `闽`）
- **PNG 预览** — PDF 处理后自动生成每页 PNG 预览图

## 安装依赖

```bash
pip install pymupdf pillow numpy requests
```

## 配置

复制 `config.json.template` 为 `config.json`，填入 API Key：

```json
{
  "qwen_api_key": "你的阿里云百炼/DashScope API Key",
  "qwen_endpoint": "https://dashscope.aliyuncs.com/compatible-mode/v1",
  "llm_disable_proxy": false
}
```

> 如使用阿里云百炼工作空间专属 endpoint，将 `qwen_endpoint` 替换为工作空间地址。

## 使用方法

```bash
# 处理单个 PDF
python scripts/localize_preserve_format.py \
  --input 原文件.pdf \
  --target-province "福建省" \
  --target-authority "福建省人力资源和社会保障厅" \
  --config config.json

# 批量处理多个文件（PDF + 图片均可）
python scripts/localize_preserve_format.py \
  --input 文件1.pdf \
  --input 文件2.pdf \
  --input 文件3.jpg \
  --target-province "福建省" \
  --target-authority "福建省人力资源和社会保障厅" \
  --config config.json

# 跳过 PNG 预览（加快速度）
python scripts/localize_preserve_format.py \
  --input 文件.pdf \
  --target-province "福建省" \
  --target-authority "福建省人力资源和社会保障厅" \
  --config config.json \
  --no-preview
```

## 参数说明

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--input` | 输入文件路径（可多次指定） | 必填 |
| `--target-province` | 目标省份名 | 必填 |
| `--target-authority` | 目标机关全称 | 必填 |
| `--config` | config.json 路径 | 必填 |
| `--output-dir` | 输出目录 | 桌面 |
| `--no-preview` | 跳过 PNG 预览生成 | 否 |
| `--font-dir` | 额外字体目录 | 无 |

## 输出说明

- **位置**：默认输出到桌面
- **命名**：`原文件名_localized.扩展名`
- **PDF 附加**：每页 PNG 预览（`原文件名_localized_page1.png` 等）

## 字体要求

脚本自动使用 Windows 系统字体：

| 字体 | 路径 |
|------|------|
| 仿宋 | `C:/Windows/Fonts/simfang.ttf` |
| 黑体 | `C:/Windows/Fonts/simhei.ttf` |
| 楷体 | `C:/Windows/Fonts/simkai.ttf` |
| 宋体 | `C:/Windows/Fonts/simsun.ttc` |

如需方正小标宋（红头专用），请将字体文件放入 `fonts/` 目录或通过 `--font-dir` 指定。

## 替换逻辑说明

LLM 只输出**省市地名本身**，不带机构职能后缀：

```
✓ 正确：广东省 → 福建省
✓ 正确：粤（发文字号中）→ 闽
✗ 不输出：广东省人力资源和社会保障厅 → 福建省人力资源和社会保障厅
```

这样可以确保替换词长度一致，避免版面错位。

## 已知限制

- 图片处理依赖 Qwen-VL API（需网络）
- 手写体不支持，仅支持印刷体公文
- 复杂嵌套表格可能有偏差

## 文件结构

```
doc-format-localizer/
├── scripts/
│   └── localize_preserve_format.py   # 主脚本
├── fonts/                             # 可选自定义字体
├── config.json                        # 配置文件（自行创建）
├── config.json.template               # 配置模板
├── SKILL.md                           # Claude Code skill 定义
└── README.md                          # 本文件
```
