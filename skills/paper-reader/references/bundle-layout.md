# 一篇论文一个阅读文件夹

放在用户项目的文献阅读目录中；沿用已有目录名，不硬编码某个用户路径。默认如下（以 FinTSB 为例，文件主名取论文短名 `FinTSB` 并保留大小写，不用 `reading`／`paper` 这类通用名）：

```text
阅读文献/FinTSB_2502.18834v3/
├── 00_导读.md                 PDF、HTML、笔记的相对链接
├── 01_原文/
│   ├── FinTSB.pdf             用户给定或实际核对过的原论文
│   └── source.html            可选，同版结构化来源快照
├── 02_阅读/FinTSB.html         英文字体、图片和交互内嵌
├── 03_笔记/阅读笔记.md          用户自己的笔记，不被重新构建覆盖
├── 04_生成/
│   ├── source.json
│   ├── annotations.json
│   └── images/...             源清单引用的本地图像，按需
└── 05_检查/
    ├── FinTSB.qa.json
    └── bundle_manifest.json   相对路径、大小和 SHA-256
```

HTML、PDF、QA 记录同名同主名：`<论文短名>.html`、`<论文短名>.pdf`、`<论文短名>.qa.json`。主名按以下顺序确定：

1. 命令行 `--slug`；
2. `source.json` 里的 `slug` 或 `short_name`；
3. 输出文件夹名去掉版本尾巴，保留原始大小写：`FinTSB_2502.18834v3` → `FinTSB`，`FinBen_NeurIPS2024` → `FinBen`，`CFBenchmark-MM_2506.13055v1` → `CFBenchmark-MM`。

多篇放在同一目录时主名必须唯一；重名就显式传 `--slug`。已有旧版 `reading.html`／`paper.pdf` 的文件夹按原清单仍可 `--check`，重命名后要同步导读、笔记和入口链接。

`build_bundle.py` 复制配套文件、构建 HTML，并检查整个文件夹。默认 HTML 链接独立 PDF，不重复嵌入 PDF；`--embed-pdf` 可同时携带。提供 PDF 时必须是真的 PDF；不提供则导读显著说明尚未收集。已有文件夹不自动覆盖，选择新版本或在核对后更新明确文件，尤其保护 03_笔记。

```bash
python <skill目录>/scripts/build_bundle.py --source source.json --annotations annotations.json --pdf paper.pdf --output-dir 阅读文献/FinTSB_2502.18834v3
python <skill目录>/scripts/build_bundle.py --check 阅读文献/FinTSB_2502.18834v3
```

目录整体可移动；所有配套链接保持相对路径。HTML 作为阅读页本身离线自足，独立 PDF 链接需要连同文件夹一起复制。文件清单记录构建时的快照，用户编辑笔记后哈希变化是正常现象，重新归档时更新清单，不能把用户新笔记覆盖为旧版来消除差异。

已有论文按旧格式生成时，保留原成品及原生成材料，按同样层次归档；如未转换到新 JSON 格式，注明旧版来源和实际重建命令，不能把仅复制叫重新生成。旧入口仍被其他笔记引用时，先保留兼容链接或入口说明，再切换主入口。
