# bili-probe

> 把 B 站视频「拆开看」：一条命令拿到 mp4、弹幕、评论、关键帧，并自动找出镜头切点、拼成接触表。

![Python](https://img.shields.io/badge/python-3.9%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Dependencies](https://img.shields.io/badge/core%20deps-0-brightgreen)

做视频二创时，最费时间的不是剪辑，而是**看懂原片**：哪几秒是重点、镜头什么时候切、观众在哪个时间点情绪最高。
`bili-probe` 把这些动作固化成了命令，跑完直接得到可用的素材和时间轴。

---

## 功能

| 命令 | 做什么 |
|---|---|
| `info` | 看标题、UP、分P、可用清晰度与编码 |
| `download` | 下载 DASH 音视频流并**无损合成** mp4 |
| `danmaku` | 抓弹幕，输出 jsonl + 高频榜 + 每 10 秒密度 |
| `comments` | 按热度抓评论（自动获取匿名 buvid，否则接口返回空） |
| `frames` | 按帧率抽帧 |
| `scenes` | 检测镜头切换，输出场景列表 |
| `sheets` | 生成 2×3 接触表，每格带时间戳 |
| `keyframes` | 按指定时间点挑最接近的关键帧 |
| `all` | 一条龙跑完全部 |

## 安装

```bash
pip install bili-probe                 # 基础功能（下载 / 弹幕 / 评论 / 抽帧）
pip install "bili-probe[sheets]"       # 额外装 Pillow + numpy，用于场景检测和接触表
pip install "bili-probe[ffmpeg]"       # 不想单独装 ffmpeg，让包自带一份
```

ffmpeg 的查找顺序：`--ffmpeg` 参数 → 环境变量 `BILI_PROBE_FFMPEG` → `PATH` → `imageio-ffmpeg`。
所以只要满足其中任意一个就能用。

## 快速开始

> **没有默认视频**：每条命令都必须手动指定目标，格式为 BV 号或完整链接。
> 下面的 `BV1Jrp16HEAB` 只是示例，换成你自己要处理的视频即可。

```bash
# 1. 先看看有什么
bili-probe info BV1Jrp16HEAB

# 2. 下载 1080P（AVC 编码，兼容性最好）
bili-probe download BV1Jrp16HEAB --quality 1080

# 3. 弹幕统计：高频词 + 时间密度
bili-probe danmaku BV1Jrp16HEAB

# 4. 找镜头切点，再拼成接触表
bili-probe scenes BV1Jrp16HEAB --threshold 4
bili-probe sheets BV1Jrp16HEAB

# 5. 想要的话，一次跑完
bili-probe all BV1Jrp16HEAB
```

`target` 是**必填项**，必须手动指定；既可以是 BV 号，也可以是完整链接（含 `?p=2` 会自动识别分P）：

```bash
bili-probe download "https://www.bilibili.com/video/BV1Jrp16HEAB/?p=2"
```

## 输出结构

```
output/
├── BV1Jrp16HEAB_p1.mp4            # 合成好的视频
├── BV1Jrp16HEAB_p1.info.json      # 元信息（清晰度、编码、时长、探测结果）
├── BV1Jrp16HEAB_p1.danmaku.jsonl  # 弹幕，每行一条 {t, text, mode, size, color}
├── BV1Jrp16HEAB_p1.danmaku.xml    # 原始弹幕 XML（已解压）
├── BV1Jrp16HEAB_p1.danmaku.txt    # 高频榜 + 密度报告
├── BV1Jrp16HEAB_p1.comments.json  # 评论
├── frames/                        # 按 --fps 抽的帧
├── scenes/
│   ├── frames/                    # 分析用帧
│   └── scenes.json                # [{start, end, file}, ...]
├── sheets/sheet01.jpg             # 接触表
└── keyframes/                     # 挑出来的关键帧
```

## 镜头是怎么判定的

没有用 AI，就是很朴素的三步，但对口播、混剪、教程类视频足够准：

1. **缩小再比较**：每帧缩到 213×120、转灰度，逐帧算差的均值；
2. **超阈值即切点**：差值大于 `--threshold`（默认 3.0）就认为切了镜头；
3. **去噪**：短于 `--min-len` 的片段丢弃（闪白、字幕跳变），灰度标准差低于 4 的丢弃（黑场、纯色）；
4. **去重**：对每个镜头的中间帧算均值哈希，和最近 4 个已保留场景相似度高于 94% 的丢掉。

参数怎么调：

| 现象 | 怎么改 |
|---|---|
| 切点太碎，同一画面被拆成好几段 | `--threshold` 调大（4～6） |
| 明显切了镜头却没检测到 | `--threshold` 调小（1.5～2） |
| 停顿很久的静态画面被切碎 | `--min-len` 调大 |
| 接触表里全是重复画面 | 检查 `dedupe`（代码内参数，默认 0.06） |

抽帧帧率也会影响精度：`--fps 2` 是默认值，快剪视频可以提到 4～5，但分析时间会成倍增加。

## 踩过的坑（都已在代码里处理）

- **弹幕不是 gzip**：`comment.bilibili.com/{cid}.xml` 返回的是 **raw deflate**，必须 `zlib.decompress(data, -15)`。用 `gzip` 解会直接抛异常。
- **评论接口需要匿名身份**：不带 cookie 请求 `/x/v2/reply/main`，`replies` 会是空的。要先访问 `/x/frontend/finger/spi` 拿到 `buvid3` / `buvid4` 塞进 Cookie。工具在初始化客户端时自动完成。
- **高清晰度要登录**：不登录一般只能拿到 480P/720P。需要 1080P 以上时用 `--sessdata` 或环境变量 `BILI_SESSDATA`。
- **不一定有 ffprobe**：很多环境只装了 ffmpeg。媒体信息改为解析 `ffmpeg -i` 的 stderr，不依赖 ffprobe。
- **请求太密会被 412**：客户端对网络错误做指数退避重试，但遇到 412 会立刻停下来提示，而不是继续撞墙。

## 限制

- 4K / 杜比 / 大会员专属清晰度需要对应账号，工具不会绕过任何付费或版权限制。
- 接口属于非公开 Web API，B 站随时可能调整字段；出问题时先跑 `bili-probe info` 看返回。
- 场景检测面向**切镜头**，对运镜、渐变转场不敏感。

## 合规声明

本项目仅用于个人学习、技术研究与**自己有权处理的内容**的备份。
请遵守 B 站用户协议与 robots 规则，控制请求频率，不要用于批量抓取或二次分发他人作品。
由使用方式产生的后果由使用者自行承担。

## 开发

```bash
git clone https://github.com/your-name/bili-probe.git
cd bili-probe
pip install -e ".[dev,sheets]"
pytest -q
```

## 许可

[MIT](LICENSE)
