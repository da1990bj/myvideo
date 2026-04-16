# Subtitle Flow Skill

视频内置字幕的完整流程，包括提取、存储、API 和前端播放。

## 调用场景

当用户询问以下问题时使用此 skill：
- "字幕流程"、"字幕提取流程"
- "字幕是怎么提取的"、"字幕怎么播放的"
- "内置字幕"、"提取字幕"
- "字幕存储"、"字幕文件命名"
- 任何关于字幕系统工作原理的问题

## 完整流程

### 1. 字幕提取 (`app/tasks.py`)

**函数**: `extract_subtitle_streams()` (第 1427-1571 行)

```
用户上传视频 → 转码任务 → FFmpeg 提取字幕流 → 存储为 VTT 文件
```

**提取逻辑**:
```python
# 1. ffprobe 探测字幕流
probe_result = subprocess.run([
    "ffprobe", "-v", "quiet", "-print_format", "json",
    "-show_streams", "-select_streams", "s", input_path
])

# 2. 从 stream tags 获取语言代码
tags = stream.get("tags") or {}
language = tags.get("language", "") or tags.get("title", "").split(" ")[0].lower()

# 3. 根据 codec 类型执行 FFmpeg 转换
# subrip/ass/ssa/mov_text → 转 WebVTT
# webvtt → 直接复制
```

**存储路径**: `/static/videos/processed/{video_id}/subtitles/`

**文件名规则**:
- `eng.vtt` - 标准语言代码
- `chi_simp.vtt` - 带地区后缀
- `track0_eng.vtt` - 同语言多字幕流
- `track6_unknown.vtt` - 未知语言

---

### 2. 字幕 API (`app/routers/videos.py`)

**端点**: `GET /videos/{video_id}/subtitles`

**返回格式**:
```json
[
  {"language": "eng", "url": "/static/videos/processed/{id}/subtitles/eng.vtt", "is_auto_generated": false},
  {"language": "chi_simp", "url": ".../chi_simp.vtt", "is_auto_generated": false}
]
```

**语言代码解析** (第 2537-2548 行):
```python
stem = vtt_file.stem  # "eng", "track6_heb", "chi_simp"
if stem.startswith("track") and "_unknown" in stem:
    lang = stem.split("_unknown")[0]  # "track6_unknown" → "track6"
elif stem.startswith("track") and "_" in stem:
    lang = stem.split("_")[1]  # "track18_heb" → "heb"
else:
    lang = stem  # "eng", "chi_simp" 直接使用
```

---

### 3. 前端加载 (`static/video.html`)

**流程**:
```
video.html 加载 → loadSubtitles() → setupSubtitleUI() → 渲染下拉菜单
```

**关键函数**:

| 函数 | 行号 | 作用 |
|------|------|------|
| `loadSubtitles()` | 2390 | 调用 API 获取字幕列表 |
| `setupSubtitleUI()` | 2403 | 渲染字幕下拉菜单 |
| `getLanguageName()` | 2434 | 转换语言代码为显示名 |
| `enableSubtitle()` | 2520 | 启用选中字幕 |
| `loadSubtitleFile()` | 2549 | fetch VTT 文件并解析 |
| `parseVTT()` | 2562 | 解析 WebVTT 时间轴 |
| `startSubtitleSync()` | 2608 | 每 100ms 更新字幕显示 |
| `disableSubtitles()` | 2634 | 禁用字幕 |

**字幕显示 HTML** (第 482-485 行):
```html
<div id="subtitle-overlay" style="position:absolute; bottom:60px; ...">
    <div id="subtitle-text" style="background:rgba(0,0,0,0.75); ..."></div>
</div>
```

---

### 流程图

```
┌─────────────────────────────────────────────────────────────┐
│  tasks.py: process_video_task()                             │
│  1. FFmpeg 转码为 HLS (h264 + AAC)                         │
│  2. extract_subtitle_streams()                              │
│     - ffprobe 探测字幕流 (codec, language, index)            │
│     - FFmpeg 转换字幕为 WebVTT                               │
│  3. update_master_playlist_with_subtitles()                 │
│     - 在 master.m3u8 中注册字幕轨道                          │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
    存储: /static/videos/processed/{video_id}/subtitles/
          ├── eng.vtt           (英文)
          ├── chi_simp.vtt      (简体中文)
          ├── chi_trad.vtt      (繁体中文)
          ├── track0_eng.vtt    (第1个英文字幕)
          └── track6_unknown.vtt (第7个未知语言)
                              │
                              ▼
    前端 video.html
                              │
                              ▼
    loadSubtitles(videoId)
    │ GET /videos/{id}/subtitles
    │ 返回: [{language, url, is_auto_generated}, ...]
    ▼
    setupSubtitleUI(subtitles)
    │ 渲染下拉菜单: "English", "简体中文", "繁体中文", ...
    ▼
    用户选择字幕 → enableSubtitle(sub)
    │ 1. disableSubtitles() - 禁用 HLS 内置字幕
    │ 2. loadSubtitleFile(url) - fetch VTT 文件
    │ 3. parseVTT(text) - 解析时间轴
    │ 4. startSubtitleSync() - 每 100ms 定时更新
    │    → 查找当前时间对应的 cue
    │    → 显示/隐藏 #subtitle-overlay
```

---

### 关键配置

| 项目 | 值 |
|------|-----|
| 字幕存储目录 | `static/videos/processed/{video_id}/subtitles/` |
| API 端点 | `GET /videos/{video_id}/subtitles` |
| 前端组件 | `#subtitle-overlay`, `#subtitle-text` |
| 同步间隔 | 100ms |
| 语言代码映射 | `video.html` 第 2436-2477 行 |

---

### 相关文件

| 文件 | 作用 |
|------|------|
| `app/tasks.py` | FFmpeg 字幕提取逻辑 |
| `app/routers/videos.py` | 字幕 API (`get_subtitles`) |
| `static/video.html` | 前端字幕加载和播放 |
| `app/config.py` | 存储路径配置 |
