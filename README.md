# Bili Audio Studio · B 站纯音频合辑工作台

把多个 B 站视频（含多 P 视频、番剧单集）的**音频**下载下来，按你排好的顺序拼成**一个**音频文件，
并在每首交界处写入**定位符（章节）**。产物可以直接连续播放、按曲跳转，同时附带 `.cue` 与
`.index.json`，方便导入其它播放器或脚本。

> **只取音频**：代码只读取 `playurl` 返回的 `dash.audio` / `flac` / `dolby`，从不请求
> `dash.video` 或 `durl`，不会把视频流下载到本地。

- 零第三方 Python 依赖：下载、HTTP 服务、界面全部使用 Python 标准库；唯一需要准备的外部程序是
  ffmpeg，界面复用系统已装的 Edge/Chrome。
- 图形界面：Python 起本地 HTTP 服务，自动用 Edge/Chrome 的**应用窗口**打开（无地址栏）。
- 本仓库只包含源码：不含任何下载音频、浏览器配置、Cookie、token 或用户目录路径。

---

## 1. 项目解决什么问题

B 站的内容通常以“视频”为单位：一个歌单可能分散在十几个视频里，多 P 视频又被拆成很多分 P。
想离线连续听时，会遇到几个麻烦：

- 官方客户端/网页不能把多个视频的音频合成一个连续文件；
- 直接下载下来的是视频，转成音频要再找工具；
- 即使把音频首尾相接，也没有“这是第几首、从几分几秒开始”的信息，跳转只能靠拖进度条；
- 想调整曲目顺序，就得重新剪辑、重新对齐时间轴。

本项目把这条链路做成一个本地工作台：

1. 粘贴 B 站链接，自动解析出视频/分 P/番剧单集的**纯音频**；
2. 并发下载并缓存，可断点续传；
3. 统一转码后按顺序**采样级精确拼接**成一个文件；
4. 自动写入容器章节、`.cue`、`.index.json` 三份定位符；
5. 在界面里点曲目即可跳到对应位置，拖动排序后重新合并即可，无需重新下载。

它适合：把系列视频/翻唱/有声书合成一个带章节的音频；给本地音乐库做一张“B 站来源”的合辑；
用 `.cue` 在 foobar2000 等播放器里按曲播放。

**它不是**：视频下载器、直播录制器、收藏夹批量搬运工具，也不提供任何绕过付费/地区限制的能力。

---

## 2. 主要功能

| 功能 | 实际行为 |
|---|---|
| 链接解析 | 支持完整视频链接、裸 `BV` 号、`av` 号、`b23.tv` 短链、`?p=N` 分 P、番剧 `ep` 链接；多 P 视频会自动展开成多条曲目，指定 `?p=N` 时只取该 P |
| 只下载音频 | 只读取 `playurl` 的 `dash.audio`，以及可用的 `dash.flac` / `dash.dolby.audio`；代码中没有任何视频流分支 |
| 音质选择 | 默认自动挑最高可用音质；可固定 `30280`（192k）、`30232`（132k）、`30216`（64k）。游客一般能拿到 192k AAC；填大会员 `SESSDATA` 后可尝试 FLAC/杜比 |
| 下载 | 默认 3 路并发（可设 1–6），`.part` 断点续传，失败自动重试并切换备用地址 |
| 统一转码 | 每首统一为 44.1 kHz 立体声 AAC，默认 192 kbps（界面可选 128/192/256），转码结果缓存在 `workspace/cache/norm/` |
| 精确拼接 | 用 ffmpeg `concat` 滤镜解码后一次性重编码，而不是直接串文件，第 N 个定位符不会因 AAC priming 逐曲累积偏移 |
| 定位符三份落地 | 音频容器的**章节**（其它播放器可见）+ 同名 `.cue` + 同名 `.index.json`；界面时间轴上也会显示刻度 |
| 可视化工作台 | 解析成功自动入列表并从输入框删除、拖拽/上下按钮排序、单曲试听、章节跳转、三种循环模式、音量记忆、运行日志、项目切换 |
| 输出格式 | 默认 `.m4a`；ffmpeg 带 `libmp3lame` 时可导出 `.mp3`（不支持时自动回退 M4A，并在日志中提示） |
| 本地服务 | 默认只监听 `127.0.0.1`，API 与音频流需要 token；支持 HTTP `Range`，界面拖动进度条不会整文件重下 |
| 运行环境 | Python 3.10+ 标准库 + ffmpeg；**不需要 pip、虚拟环境或任何第三方 Python 包** |

界面快捷键：`空格` 播放/暂停，`N` 下一首，`P` 上一首，`L` 切换循环，`M` 静音，`↑`/`↓` 音量 ±5%，
`←`/`→` 前后 5 秒。循环按钮按 **顺序播放 → 列表循环 → 单曲循环** 轮换；单曲循环在整条合辑里
依靠定位符的起止时间精确回跳，不会串到下一首。

---

## 3. 安装方法

### 3.1 环境要求

| 项目 | 要求 |
|---|---|
| 操作系统 | 主要目标为 Windows 10/11。`run.bat` 仅 Windows 可用；其它平台可 `python app.py`，但需自备 ffmpeg 和浏览器 |
| Python | **3.10 或更高版本**，且 `python` 在 `PATH` 中 |
| ffmpeg | 必需。仓库不附带二进制，首次运行会自动下载；也可以手动准备或用 `FFMPEG_BINARY` 指定 |
| Python 包 | **无需安装**。没有 `requirements.txt`，不要执行 `pip install` |
| Node.js | 仅 `scripts/ui_check.mjs` 需要（Node 18+）；主程序不依赖 |

### 3.2 获取代码并进入项目根目录

```powershell
git clone <你的仓库地址>
cd <项目目录>
python --version
```

应输出 `Python 3.10` 或更高版本。下面的命令都在**项目根目录**执行。

如果不想用 Git，也可以下载 ZIP 解压后，在解压出的项目根目录打开 PowerShell。

### 3.3 准备 ffmpeg

首次运行 `run.bat` 或 `app.py` 时，如果 `vendor\ffmpeg\ffmpeg.exe` 不存在，会自动下载并解压
Windows 版 ffmpeg。也可以手动执行：

```powershell
python scripts\fetch_ffmpeg.py
```

脚本会从几个 Windows 静态构建源分段下载 `ffmpeg.exe`、`ffprobe.exe` 及相关 DLL 到
`vendor\ffmpeg\`。如果本机已经有 ffmpeg，也可以直接指定：

```powershell
$env:FFMPEG_BINARY = (Get-Command ffmpeg).Source
```

或者把 `ffmpeg.exe` 和依赖 DLL 放进 `vendor\ffmpeg\`。

校验：

```powershell
.\vendor\ffmpeg\ffmpeg.exe -version
```

### 3.4 安装校验

不需要 `pip install`，直接检查入口和帮助信息：

```powershell
python app.py --help
```

应看到 `--port` 与 `--no-open` 两个参数。

---

## 4. 使用方法

### 4.1 启动

双击 `run.bat`（它会检查 Python、按需补 ffmpeg，再启动 `app.py`），或在 PowerShell 中：

```powershell
.\run.bat
```

只启动 Python 入口：

```powershell
python app.py
```

其它启动方式：

```powershell
python app.py --no-open          # 只起服务，不自动打开窗口
python app.py --port 9000        # 指定端口（被占用时会继续尝试后续端口）
```

启动后终端会打印界面地址，并自动用 Edge/Chrome 的 `--app` 应用窗口打开（找不到 Chromium 系
浏览器时退回默认浏览器标签页）。默认端口从 `8787` 开始，被占用时自动往后找。

> `--no-open` 时终端只打印 `http://127.0.0.1:<端口>/`，API 和音频流还需要 token。
> token 在首次启动时生成，保存在 `workspace\settings.json` 的 `token` 字段；
> 在浏览器地址后加 `?t=<token>` 即可。直接 `python app.py` 启动时，程序会自动把 token
> 拼进打开的地址。

运行时目录：

- `workspace\`：设置、项目、缓存、输出、浏览器配置；首次启动自动创建；
- `vendor\`：ffmpeg 二进制；首次运行自动创建。

这两个目录都已被 `.gitignore` 忽略，**不要提交到仓库**。

### 4.2 界面操作流程

1. 在左侧输入框粘贴链接，**每行一个**，一次最多解析 60 行；
2. 点 **解析并添加**：解析成功的行会进入曲目列表并从输入框删除，失败的行留在框里；
   界面按 `bvid + cid` 自动跳过重复曲目；
3. 在曲目列表里拖动 `⋮⋮`，或用 `↑`/`↓` 调整顺序；`✕` 删除；行首 `▶` 播放/试听单曲；
4. 点 **下载并合并**：下载 → 转码 → 拼接 → 写章节/`.cue`/`.index.json` 一条龙完成；
   **仅下载**只补齐音频，不生成合辑；**强制重建**会忽略转码缓存重新来一遍；
5. 在右侧播放器播放合辑：点曲目行会跳到该曲起点，点时间轴可跳转，`N`/`P` 切上一首/下一首；
6. 改完顺序后再次点 **下载并合并** 即可，已下载/已转码的曲目会复用缓存，只重新拼接。

> 还没合并进当前合辑的曲目，点行首 `▶` 是**单曲试听**（直接播放该曲缓存）；已经合并的曲目，
> 点击会跳到整条合辑中的对应位置。

### 4.3 设置项

点右上角 **⚙ 设置**，可修改：

| 设置 | 说明 | 默认 |
|---|---|---|
| B 站 Cookie（SESSDATA） | 可选。填大会员 `SESSDATA` 可尝试 FLAC/杜比；只保存在本机 `workspace\settings.json` | 空（游客） |
| 音质偏好 | 自动最高 / 192k / 132k / 64k | 自动 |
| 输出格式 | M4A（推荐）或 MP3（需要 ffmpeg 带 `libmp3lame`） | M4A |
| 输出码率 | 128 / 192 / 256 kbps | 192 |
| 并发下载数 | 1–6 | 3 |
| 输出目录 | 留空 = `workspace\output`；建议填一个绝对路径 | 空 |

项目名可以点顶部项目名直接改；**项目** 按钮可新建/打开历史项目。项目、曲目顺序、缓存路径都保存在
`workspace\projects\<项目ID>.json`。

音量和循环模式保存在浏览器 `localStorage`（应用窗口使用 `workspace\browser-profile` 这个独立配置目录），
下次打开仍是上次的值。

> `workspace\browser-profile` 是完整的浏览器用户目录，可能包含 Cookie、登录态和浏览历史。
> 它只用于本机运行，已被 `.gitignore` 忽略；发布项目或换机前请整体删除。

### 4.4 通过 HTTP API 使用（可选）

界面本身就是调用这些 REST 接口。以 `--no-open --port 9123` 启动后：

```powershell
$token = (Get-Content .\workspace\settings.json -Raw | ConvertFrom-Json).token

# 读取当前项目、曲目、定位符、ffmpeg 状态
Invoke-RestMethod -Uri 'http://127.0.0.1:9123/api/state' `
  -Headers @{ 'X-Token' = $token }
```

主要接口：

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/api/state` | 当前项目、曲目、定位符、后台任务、ffmpeg 状态、设置（不含 token） |
| POST | `/api/parse` | 解析链接，body 为 `{"urls": "..."}` |
| POST | `/api/tracks` | 把解析结果加入项目，body 含 `items`、可选 `skip_duplicates` |
| POST | `/api/tracks/reorder` | 调整顺序，body 为 `{"order": [trackId, ...]}` |
| DELETE | `/api/tracks/<id>` | 删除一首 |
| POST | `/api/download` | 后台下载全部未完成曲目 |
| POST | `/api/build` | 后台下载并合并，body 可带 `{"force": true}` |
| GET | `/api/jobs/<id>` | 查询任务进度、日志、结果 |
| POST | `/api/jobs/<id>/cancel` | 取消任务 |
| GET | `/api/output` | 当前产物信息与播放地址 |
| GET | `/media/output?t=<token>` | 合辑音频流（支持 `Range`） |
| GET | `/media/track?id=<id>&t=<token>` | 单曲试听流（支持 `Range`） |

鉴权：请求头 `X-Token` 或查询参数 `?t=` 二者其一。token 首次启动自动生成，不会为空。

### 4.5 测试与自检

在项目根目录执行。

**离线单元/HTTP/界面资源测试（不需要联网）**：

```powershell
python -m unittest discover -s tests -v
```

共 **48 项**，覆盖链接解析、文件名清洗、CUE/索引时间码、Range、项目排序、HTTP 鉴权、
界面资源一致性。测试使用临时目录，不会写入真实的 `workspace\settings.json`。

**真实联网端到端自检**：

```powershell
python -X utf8 scripts\smoke_test.py
```

`-X utf8` 是必要的：脚本会打印 `✓`/`✗`，Windows 默认 GBK 控制台下不加该参数会抛
`UnicodeEncodeError`。它会从热门列表挑选短视频，跑完整流程并检查：纯音频 fMP4、定位符偏移、
总时长、容器章节、cue/index 结构、重排后复用缓存、MP3 导出。

可选参数：

```powershell
python -X utf8 scripts\smoke_test.py --fresh              # 清掉 .smoke 下载缓存重来
python -X utf8 scripts\smoke_test.py --keep               # 保留 .smoke 中间文件
```

在命令末尾追加一个或多个真实 BVID，可以指定要测试的视频。

自检数据写在 `workspace\.smoke\`，不会污染正式的 `workspace\output\`。

**界面运行时自检（可选，需要 Node 18+）**：

```powershell
node scripts\ui_check.mjs
```

它用最小 DOM 替身在 Node 里真实执行 `app.js`，可发现导致白屏的运行时错误。没有 Node 时跳过即可。

---

## 5. 输入输出示例

以下均为**字段示例**，实际标题、时长、音质以接口和运行结果为准。

### 5.1 输入

在界面左侧输入框每行粘贴一个链接，支持以下形式（下面只是格式示例，不保证示例链接可访问）：

```text
https://www.bilibili.com/video/BV1xx411c7mD
BV1xx411c7mD
https://www.bilibili.com/video/av170001
https://www.bilibili.com/video/BV1xx411c7mD?p=2
https://b23.tv/xxxxxx
https://www.bilibili.com/bangumi/play/ep123456
```

其中：

- 多 P 视频会展开成多首；带 `?p=2` 时只取第 2 P；
- `b23.tv` 短链会先跟随跳转再解析；
- 解析成功的行会从输入框自动删除，失败的保留在原位。

也可以直接调用解析接口：

```powershell
$token = (Get-Content .\workspace\settings.json -Raw | ConvertFrom-Json).token
Invoke-RestMethod -Uri 'http://127.0.0.1:9123/api/parse' -Method Post `
  -Headers @{ 'X-Token' = $token } -ContentType 'application/json' `
  -Body '{"urls":"BV1xx411c7mD"}'
```

返回结构（字段示例）：

```json
{
  "items": [
    {
      "ok": true,
      "input": "BV1xx411c7mD",
      "bvid": "BV1xx411c7mD",
      "aid": 1,
      "cid": 11,
      "page": 1,
      "title": "示例视频",
      "part": "示例视频",
      "owner": "示例 UP 主",
      "duration": 180.0,
      "source_url": "https://www.bilibili.com/video/BV1xx411c7mD?p=1",
      "display": "示例视频"
    }
  ]
}
```

### 5.2 输出位置

默认输出到 `workspace\output\`，文件名由项目名清洗后加扩展名：

```text
workspace/
├─ cache/
│  ├─ BV1xx411c7mD_11_30280.m4s      # 原始音频流
│  └─ norm/<track-id>.m4a             # 统一转码后的拼接素材
├─ output/
│  ├─ 示例合辑.m4a                    # 完整合辑音频（容器内带章节）
│  ├─ 示例合辑.cue                    # CUE 索引
│  └─ 示例合辑.index.json             # 时间轴数据源
├─ projects/
│  └─ <project-id>.json               # 曲目、顺序、缓存路径、定位符
└─ settings.json                      # token、SESSDATA、音质、码率、输出目录等
```

每次合并会生成三件套：

| 文件 | 内容 |
|---|---|
| `<项目名>.m4a` / `.mp3` | 完整合辑音频，容器内写有章节 |
| `<项目名>.cue` | 供 foobar2000 等播放器按曲播放的索引 |
| `<项目名>.index.json` | 时间轴数据源：标题、每首起点/时长、BVID/CID/音质 |

### 5.3 `index.json` 示例

两首示例曲目：

```json
{
  "version": 1,
  "title": "示例合辑",
  "audio": "示例合辑.m4a",
  "duration": 360.0,
  "markers": [
    {
      "index": 0,
      "title": "示例曲目 A",
      "start": 0.0,
      "duration": 180.0,
      "start_text": "0:00"
    },
    {
      "index": 1,
      "title": "示例曲目 B",
      "start": 180.0,
      "duration": 180.0,
      "start_text": "3:00"
    }
  ],
  "tracks": [
    {
      "title": "示例曲目 A",
      "bvid": "BV1xx411c7mD",
      "cid": 11,
      "url": "https://www.bilibili.com/video/BV1xx411c7mD?p=1",
      "quality": "192k",
      "duration": 180.0
    },
    {
      "title": "示例曲目 B",
      "bvid": "BV1xx411c7mE",
      "cid": 22,
      "url": "https://www.bilibili.com/video/BV1xx411c7mE?p=1",
      "quality": "192k",
      "duration": 180.0
    }
  ]
}
```

### 5.4 `cue` 示例

```cue
REM GENRE "Bilibili"
PERFORMER "示例 UP 主"
TITLE "示例合辑"
FILE "示例合辑.m4a" M4A
  TRACK 01 AUDIO
    TITLE "示例曲目 A"
    PERFORMER "示例 UP 主"
    INDEX 01 00:00:00
  TRACK 02 AUDIO
    TITLE "示例曲目 B"
    PERFORMER "示例 UP 主"
    INDEX 01 03:00:00
```

> `.cue` 的 `FILE` 行目前固定写作 `M4A`。导出 MP3 时仍会生成 `.cue` 和 `.index.json`，
> 但若要在只认扩展名的播放器里使用，请把 `FILE` 行末的类型改为 `MP3`。M4A 是推荐格式。

### 5.5 验证产物

用下载好的 ffprobe 查看时长与章节：

```powershell
.\vendor\ffmpeg\ffprobe.exe -v error -show_entries format=duration -show_chapters `
  -of json .\workspace\output\示例合辑.m4a
```

输出中的 `chapters` 数量应与 `index.json` 的 `markers` 数量一致，`start_time` 应与
`markers[].start` 对齐。

只看文件列表：

```powershell
Get-ChildItem .\workspace\output
```

---

## 6. 目录结构

```text
.
├─ app.py                      # 入口：起服务 + 打开应用窗口
├─ run.bat                     # 双击启动（检查 Python、自举 ffmpeg）
├─ biliaudio/
│  ├─ config.py                # 路径、设置、文件名清洗、原子写入
│  ├─ net.py                   # urllib 客户端（Cookie/重试/断点续传/短链）
│  ├─ bilibili.py              # 链接解析、view/playurl、音质挑选
│  ├─ pipeline.py              # 下载 → 转码 → 拼接 → 侧车文件
│  ├─ audio.py                 # ffmpeg 封装、章节、cue/index 生成
│  ├─ project.py               # 项目模型、排序、定位符计算
│  ├─ jobs.py                  # 后台任务、进度、日志、取消
│  ├─ server.py                # REST API + Range 音频流 + token 鉴权
│  └─ web/                     # 原生 HTML/CSS/JS 界面（无框架）
├─ scripts/
│  ├─ fetch_ffmpeg.py          # 下载 ffmpeg 到 vendor/ffmpeg
│  ├─ smoke_test.py            # 真实联网端到端自检
│  └─ ui_check.mjs             # Node 界面运行时自检（可选）
├─ tests/                      # 48 项单元/HTTP/界面资源测试
├─ README.md
├─ LICENSE                     # MIT
└─ .gitignore
```

运行时自动创建、且已被忽略：

```text
workspace/                     # 设置、项目、缓存、输出、浏览器配置
vendor/                        # ffmpeg.exe / ffprobe.exe / DLL
```

---

## 7. 常见问题

**没有网络 / 下载 ffmpeg 失败？**
手动下载任意 Windows 版 ffmpeg（含 `ffmpeg.exe` 与 DLL），解压后放进 `vendor\ffmpeg\`；
或设置环境变量 `FFMPEG_BINARY` 指向已有的 `ffmpeg.exe`。没有 ffmpeg 时仍能解析和下载音频，
但不能转码/合并。

**提示“该视频没有可用音轨”？**
该稿件没有音频流，或音频被限制。换一个视频；番剧需要对应权限。

**端口被占用？**
程序会自动从 `8787` 往后尝试最多 20 个端口；也可以 `python app.py --port 9100`。

**关闭窗口后服务还在跑？**
关掉启动它的黑色命令行窗口，或在其中按 `Ctrl+C`。只关浏览器应用窗口不会停止 Python 服务。

**想换输出目录？**
设置里填一个绝对路径，例如你本机的音乐目录。留空则用 `workspace\output`。

**移动了程序文件夹？**
项目 JSON 里存的是之前缓存的绝对路径。重新点一次 **下载并合并** 即可按新位置重建；
下载缓存若还在，会尽量复用。

**MP3 选项是灰的？**
当前 ffmpeg 没有 `libmp3lame`。用 M4A 即可，或在设置里换一个带 MP3 编码器的 ffmpeg。

**控制台中文/符号乱码？**
`run.bat` 会执行 `chcp 65001`；手动运行 `smoke_test.py` 时用 `python -X utf8 ...`。
乱码只影响显示，不影响产物。

**`workspace/` 可以提交吗？**
不要。它包含 `settings.json`（token/SESSDATA）、`browser-profile`（Cookie/登录态/历史）、
下载缓存和音频。`.gitignore` 已排除；发布前请确认没有用 `git add -f` 强制加入。

**测试会写我的设置吗？**
不会。HTTP 集成测试把设置文件重定向到临时目录，并使用随机 token，不会修改真实的
`workspace\settings.json`。

---

## 8. 实现要点（给维护者）

- **不碰视频**：`bilibili.attach_audio()` 只读取 `playurl` 的 `dash.audio` / `flac` / `dolby`，
  代码中没有 `dash.video`、`durl` 的任何分支。
- **采样级拼接**：`audio.merge()` 用 `-filter_complex concat` 解码后一次性重编码，再写章节；
  不是直接串文件，所以不会因 AAC priming 逐曲累积偏移。
- **缓存分层**：`cache/*.m4s`（原始流）与 `cache/norm/*.m4a`（统一转码）分开；
  改顺序只重跑“拼接”这一步，`smoke_test.py` 会验证重建没有重新下载。
- **定位符来源**：每首先 `probe_duration()` 得到真实时长，再累加算出起点；同一组 `markers`
  同时写进容器章节、`.cue` 和 `.index.json`。
- **界面无框架**：`web/` 是原生 HTML/CSS/JS，通过带 token 的本地 HTTP API 通信；
  `<audio>` 的 Range 请求由 `server.py` 的 `parse_range()` 支持。
- **安全边界**：服务默认只绑定 `127.0.0.1`；API 和 `/media/*` 校验 token；
  静态文件路径做了 `relative_to(WEB_DIR)` 越界检查。
- **测试隔离**：HTTP 测试把项目目录、当前项目文件和设置文件都重定向到临时目录，
  使用随机 token，避免污染真实运行数据。

---

## 9. 许可证与第三方说明

本项目使用 **MIT License**，版权归 **YMYC** 所有，详见 [LICENSE](LICENSE)。

ffmpeg 是独立的第三方程序，不在本仓库中分发；首次运行由脚本下载到 `vendor/`。使用、分发
ffmpeg 时需遵守其自身许可证（本项目下载脚本默认使用 GPL shared 构建，具体以实际构建为准）。

本项目不包含任何 B 站音频、视频、封面或登录凭据。下载内容仅供个人离线收听与备份，
请遵守哔哩哔哩的用户协议与著作权规定，不要用于二次分发、商业用途或任何绕过付费/加密/地区限制
的场景。所有内容版权归原作者与平台所有。
