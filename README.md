# html2any

一个 URL，一次抓取，三种产物：**markdown** / **分页长图** / **结构化 JSON 树**。

- `markdown`：正文转 markdown，页面图片下载到 `imgs/` 并改写相对路径
- `images`：渲染成分页 PNG（auto 模式按空白行智能分页，不切断内容；page 模式由排版引擎分页）
- `structure`：按 h1~h6 层级递归建树成 `result.json`，每个节点附快照图

## 安装

```bash
# 方式 A：conda 环境复用（本机已有 py12，依赖齐全）
C:\Users\18372\.conda\envs\py12\python.exe -c "import requests, bs4, markdownify, PIL"

# 方式 B：标准 venv（禁止使用 uv）
C:\Users\18372\.workbuddy\binaries\python\versions\3.13.12\python.exe -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

外部依赖：本机已安装 Chrome 或 Edge（自动探测，可用 `--chrome` 或 `.env` 的 `CHROME_PATH` 指定）。

## 用法

```bash
python main.py <url>                            # 三种方式全跑
python main.py <url> --modes markdown           # 只跑 markdown
python main.py <url> --modes markdown,structure # 只跑两种
python main.py <url> --force                    # 忽略断点续传，重跑选中方式
python main.py list                             # 列出已有运行记录
```

常用参数（全部见 `python main.py -h`）：

- 共享：`--main <选择器>`（指定正文容器）、`--font-size`、`--max-page-height`、`--chrome`
- markdown：`--no-images`（不下载图片）
- images：`--image-mode auto|page`、`--scale-factor 2`（2 倍图）、`--keep-full`
- structure：`--levels 2-4`、`--images`（额外落 imgs/）、`--no-snapshot`（只出 JSON）

## 快捷脚本

`scripts/` 下每个文件就是一行完整命令（无变量、无参数透传）。
打开文件、把里面的 url 换成目标页面，然后在仓库根目录执行（Windows 下用 Git Bash）：

```bash
sh scripts/md.sh     # 只跑 markdown
sh scripts/img.sh    # 只跑 images
sh scripts/struct.sh # 只跑 structure
sh scripts/all.sh    # 三种全跑
sh scripts/list.sh   # 列出已有运行记录
```

需要更多选项（如 `--force`、`--main`）时，直接照着脚本里的命令补参数运行即可。

## 输出结构

```
outputs/
└── 20260913_a1b2c3d4/          # run_id = 日期 + 归一化 url 的 8 位哈希
    ├── meta.json               # 完整 url、标题、抓取时间、各方式完成状态（断点续传依据）
    ├── source.html             # 原始 html，三种方式共用一次网络请求
    ├── markdown/
    │   ├── article.md          # 图片以 ![alt](imgs/001.png) 相对路径插入
    │   └── imgs/001.png ...
    ├── images/
    │   ├── 001.png 002.png ... # 分好页的多张图（meta.json 里有每页尺寸）
    │   ├── full.png            # 仅 --keep-full（裁白前的整页长图）
    │   └── page.pdf            # 仅 --image-mode page（默认保留，--no-pdf 关掉）
    └── structure/
        ├── result.json         # {"tree": [递归树]}，每节点 content/clean_content/snapshot_path
        ├── snapshots/0_<标题>_part1.png ...
        └── imgs/001.png ...    # 仅 --images
```

## 断点续传

目录名不再使用 URL（又长又没意义），改用 `run_id` 短哈希（同 `20260913_a1b2c3d4`）：

- 同一 URL 重跑自动命中同一 run 目录（归一化时去掉 utm 等跟踪参数和尾部斜杠）；
- 重跑时若 `source.html` 已存在则直接复用，不重新请求网络；
- `meta.json` 里 `status=done` 的方式自动跳过，`error` 的自动重试（`--force` 全部重跑）；
- structure 方式内部还有文件级续传：已有 `{节点}_part*.png` 的节点不再重新截图。

## 配置（.env）

复制 `.env.example` 为 `.env`，可设置 `OUTPUT_DIR` / `CHROME_PATH` / `HTTP_TIMEOUT` / `HTTP_RETRIES`。
CLI 参数优先于 `.env`。

## 项目结构

```
main.py                  # 统一入口
html2any/
├── config.py            # .env 读取
├── fetch.py             # requests 抓取 + 重试 + 编码（三方式共用）
├── extract.py           # 正文抽取 + 图片本地化（三方式共用）
├── chrome.py            # Chrome 探测 / 无头截图 / 测高测宽 / PDF（二、三共用）
├── paginate.py          # 四向裁白 + 空白行智能分页（二、三共用）
├── runid.py             # url -> run_id 短哈希
├── meta.py              # meta.json 读写（断点续传）
└── modes/
    ├── to_markdown.py   # 方式一
    ├── to_images.py     # 方式二
    └── to_structure.py  # 方式三
scripts/                 # 常用组合的一行式 sh（打开改 url 后直接执行）
```
