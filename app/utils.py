import re
import os

# Load sensitive words
SENSITIVE_WORDS = set()
WORD_FILE = "/data/myvideo/data/sensitive_words.txt"

if os.path.exists(WORD_FILE):
    with open(WORD_FILE, "r", encoding="utf-8") as f:
        for line in f:
            w = line.strip().lower()
            if w: SENSITIVE_WORDS.add(w)

def clean_tags(tags: list[str]) -> list[str]:
    cleaned = []
    seen = set()

    for t in tags:
        # 1. 基础清洗
        t = t.strip().lower()
        if not t: continue

        # 2. 长度限制 (1-20)
        if len(t) > 20:
            # 超过长度直接丢弃，避免截断后产生歧义
            continue

        # 3. 特殊字符过滤 (允许中文、字母、数字、下划线、空格、横杠、点、加号、井号)
        # 允许 C++, C#, .NET, Node.js 等技术标签
        t = re.sub(r'[^\w\u4e00-\u9fa5\s\-\.\+\#]', '', t)
        if not t.strip(): continue

        # 4. 去重
        if t in seen: continue

        # 5. 敏感词过滤
        is_bad = False
        for bad in SENSITIVE_WORDS:
            if bad in t: # 简单的包含匹配
                is_bad = True
                break
        if is_bad: continue

        seen.add(t)
        cleaned.append(t)

        # 6. 数量限制 (Max 5 - Updated for Architecture Upgrade)
        if len(cleaned) >= 5: break

    return cleaned
