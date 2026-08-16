# 政府公文格式保留本地化工具

将外省政府公文（PDF/图片）的省份、机关等地域信息替换为福建省对应信息，**完全保留原始版面、字体、颜色和排版**。

## 功能特点

### 支持的输入格式
- **PDF文件** - 直接在PDF文字层操作，精确替换
- **图片文件** - JPG/PNG/BMP等，支持扫描件和截图

### 核心功能
1. **格式完全保留** - 原版面、字体、字号、颜色、行距、边距完全不变
2. **智能字体识别** - 自动识别红头（方正小标宋）、正文（仿宋）、标题（黑体）
3. **自动地域替换** - 省份名、机关名、文号代字自动转换
4. **发文文号规范** - 括号统一转换为方头括号 `〔〕`
5. **PNG预览生成** - PDF处理后自动生成每页PNG预览图

## 安装依赖

```bash
pip install pymupdf pillow numpy rapidocr-onnxruntime requests dashscope
```

## 使用方法

### 基本用法

```bash
# 处理图片
python scripts/localize_preserve_format.py \
  --input 原文件.jpg \
  --target-province "福建省" \
  --target-authority "福建省人力资源和社会保障厅" \
  --config config.json

# 处理PDF
python scripts/localize_preserve_format.py \
  --input 原文件.pdf \
  --target-province "福建省" \
  --target-authority "福建省人力资源和社会保障厅" \
  --config config.json
```

### 批量处理

```bash
# 处理多个文件
python scripts/localize_preserve_format.py \
  --input 文件1.jpg \
  --input 文件2.pdf \
  --input 文件3.jpg \
  --target-province "福建省" \
  --target-authority "福建省人力资源和社会保障厅" \
  --config config.json
```

### 配置文件

在 `config.json` 中配置千问API密钥（用于图片字体识别）：

```json
{
  "dashscope_api_key": "your-api-key-here"
}
```

## 输出说明

- **输出位置**: 桌面 (`~/Desktop/`)
- **文件命名**: `原文件名_localized.扩展名`
- **PDF额外输出**: 每页PNG预览图（`原文件名_localized_page1.png`等）

## 技术原理

### PDF处理流程
1. PyMuPDF提取文字span的精确坐标、字号、颜色
2. 使用redact删除原文字
3. 用系统字体在原坐标写入新文字

### 图片处理流程
1. Qwen-VL识别各文字块的字体、位置、颜色
2. RapidOCR进行字符级精确定位
3. PIL像素级白色覆盖 + 新文字渲染

## 特殊处理

### 红头处理
- 自动识别方正小标宋字体
- 保持原字号或按红线宽度自动缩小
- 字间距自适应，避免负间距

### 发文文号
- 所有括号变体 `（）()[]{【】{}` 统一转换为 `〔〕`
- 补丁右边扩展3个字宽度，确保完整覆盖

### 正文和标题
- 整行作为一个补丁统一处理
- 保持原有字体、字号、居中对齐

## 已知限制

1. **图片处理依赖Qwen-VL API** - 需要网络连接，可能较慢
2. **复杂表格支持有限** - 简单表格可处理，复杂多层表格可能有偏差
3. **手写体不支持** - 仅支持印刷体公文
4. **标题多行整体补丁** - 暂未完全实现，多行标题目前逐行处理

## 文件结构

```
doc-format-localizer/
├── scripts/
│   └── localize_preserve_format.py    # 主脚本
├── config.json                         # 配置文件（需自行创建）
├── SKILL.md                           # Claude Code skill定义
└── README.md                          # 本文件
```

## 配置字体

脚本会自动查找系统字体，支持的字体：
- 方正小标宋 (FZXBSK)
- 仿宋 (FangSong_GB2312)
- 黑体 (SimHei)
- 宋体 (SimSun)
- 楷体 (KaiTi_GB2312)

如需使用自定义字体目录：
```bash
--font-dir /path/to/fonts
```

## 常见问题

**Q: 输出文件在哪里？**  
A: 统一输出到桌面 (`~/Desktop/`)

**Q: 如何跳过Qwen-VL加快处理？**  
A: 第一次处理后会缓存 `_blocks.json`，第二次处理同一文件可加 `--skip-vision` 跳过

**Q: PDF生成PNG太多怎么办？**  
A: 加 `--no-preview` 跳过PNG生成

**Q: 某些字没有被替换？**  
A: 检查是否在替换词表中，可能需要调整LLM生成的词表逻辑

## 更新日志

### v1.0 (2026-08)
- 初始版本
- 支持PDF和图片处理
- 红头字体自适应
- 发文文号括号规范化
- 整行补丁模式
- 输出统一到桌面

## 联系方式

如有问题或建议，请联系开发团队。
