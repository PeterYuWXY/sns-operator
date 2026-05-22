"""Detect AI-generated tone patterns. Score 0-100 (0 = human, 100 = obvious AI)."""
import re
from collections import Counter
from typing import Dict, List, Tuple

# Each entry: (pattern, category, penalty)
_PATTERNS_CN: List[Tuple[str, str, int]] = [
    # 套话
    (r"值得注意的是", "套话", 10),
    (r"不容忽视", "套话", 8),
    (r"毋庸置疑", "套话", 10),
    (r"总的来说", "套话", 10),
    (r"综上所述", "套话", 10),
    (r"不得不说", "套话", 8),
    (r"显而易见", "套话", 8),
    (r"众所周知", "套话", 8),
    (r"与此同时", "套话", 6),
    (r"在此背景下", "套话", 8),
    (r"从某种程度上说", "套话", 8),
    (r"不言而喻", "套话", 8),
    (r"换句话说", "套话", 6),
    (r"总而言之", "套话", 8),
    (r"简而言之", "套话", 8),
    (r"不可否认", "套话", 8),
    (r"从全局来看", "套话", 8),
    (r"在此基础上", "套话", 6),
    (r"进一步来说", "套话", 6),
    (r"值得一提的是", "套话", 10),
    (r"值得关注的是", "套话", 10),
    (r"尤其值得注意", "套话", 10),
    (r"毫无疑问", "套话", 8),
    (r"可以预见", "套话", 6),
    (r"不难发现", "套话", 6),
    (r"不难看出", "套话", 6),
    (r"由此可见", "套话", 6),
    (r"不禁让人", "套话", 8),
    (r"引人深思", "套话", 8),
    (r"发人深省", "套话", 8),
    (r"我们不禁要问", "套话", 10),
    # AI 句式
    (r"不仅.*而且", "AI句式", 8),
    (r"一方面.*另一方面", "AI句式", 8),
    (r"虽然.*但是", "AI句式", 5),
    (r"首先.*其次.*最后", "AI句式", 8),
    (r"第一.*第二.*第三", "AI句式", 8),
    # 书面词汇
    (r"显著提升", "书面词汇", 8),
    (r"充分利用", "书面词汇", 8),
    (r"旨在", "书面词汇", 8),
    (r"致力于", "书面词汇", 8),
    (r"旨在.*实现", "书面词汇", 8),
    # AI 写作特征
    (r"让我们", "AI特征", 8),
    (r"本文.*", "AI特征", 8),
    (r"笔者.*", "AI特征", 8),
    (r"作者.*认为", "AI特征", 8),
    (r"随着.*的发展", "AI特征", 8),
    (r"在当今时代", "AI特征", 8),
    (r"随着.*时代", "AI特征", 8),
    (r"当今社会", "AI特征", 8),
    (r"新时代.*背景", "AI特征", 8),
    (r"深度.*分析", "AI特征", 6),
    (r"全面.*解析", "AI特征", 6),
    (r"深度.*解读", "AI特征", 6),
    (r"一文.*读懂", "AI特征", 6),
    # 态度中立
    (r"具体取决于实际情况", "态度中立", 8),
    (r"各有优劣", "态度中立", 8),
    (r"需要综合考虑", "态度中立", 5),
    # 列表结构
    (r"一、", "结构机械", 5),
    (r"二、", "结构机械", 5),
    (r"三、", "结构机械", 5),
    (r"（一）", "结构机械", 5),
    (r"（二）", "结构机械", 5),
]

_PATTERNS_EN: List[Tuple[str, str, int]] = [
    (r"it'?s worth noting", "cliché", 10),
    (r"it is worth mentioning", "cliché", 10),
    (r"needless to say", "cliché", 8),
    (r"in conclusion", "cliché", 10),
    (r"in summary", "cliché", 10),
    (r"to summarize", "cliché", 8),
    (r"furthermore", "cliché", 6),
    (r"moreover", "cliché", 6),
    (r"additionally", "cliché", 6),
    (r"it'?s important to note", "cliché", 10),
    (r"it should be noted", "cliché", 10),
    (r"undoubtedly", "cliché", 8),
    (r"without a doubt", "cliché", 8),
    (r"there'?s no denying", "cliché", 8),
    (r"as we all know", "cliché", 8),
    (r"it goes without saying", "cliché", 8),
    (r"let me", "AI特征", 8),
    (r"let'?s explore", "AI特征", 8),
    (r"let'?s dive", "AI特征", 8),
    (r"in this article", "AI特征", 8),
    (r"in this post", "AI特征", 8),
    (r"today we will", "AI特征", 8),
    (r"the bottom line", "cliché", 6),
    (r"at the end of the day", "cliché", 6),
    (r"when all is said and done", "cliché", 8),
    (r"all things considered", "cliché", 8),
    (r"having said that", "cliché", 6),
    (r"that being said", "cliché", 6),
    (r"generally speaking", "cliché", 6),
    (r"broadly speaking", "cliché", 6),
    (r"to be honest", "cliché", 5),
    (r"first(ly)?.*second(ly)?.*third(ly)?", "AI句式", 8),
    (r"firstly.*secondly.*lastly", "AI句式", 8),
    (r"in the first place", "AI句式", 6),
    (r"last but not least", "cliché", 8),
    (r"deep dive|dive deep", "cliché", 6),
    (r"unpack", "cliché", 6),
    (r"delve into", "cliché", 6),
    (r"game.?changer", "cliché", 6),
    (r"paradigm shift", "cliché", 6),
]

# Hedging / neutral language (indicates wishy-washy AI output)
_NEUTRAL_CN = [r"可能", r"也许", r"大概", r"或许", r"不一定"]
_NEUTRAL_EN = [r"\bmaybe\b", r"\bperhaps\b", r"\bpossibly\b", r"\bapparently\b"]


def detect(text: str) -> Dict:
    issues: List[str] = []
    raw_score = 0

    for pattern, category, penalty in _PATTERNS_CN:
        matches = re.findall(pattern, text, re.IGNORECASE)
        if matches:
            raw_score += penalty * len(matches)
            issues.append(f"[{category}] {pattern} ×{len(matches)}")

    tl = text.lower()
    for pattern, category, penalty in _PATTERNS_EN:
        matches = re.findall(pattern, tl)
        if matches:
            raw_score += penalty * len(matches)
            issues.append(f"[{category}] {pattern} ×{len(matches)}")

    # Neutral/hedging language accumulation
    neutral_count = sum(len(re.findall(p, text)) for p in _NEUTRAL_CN)
    neutral_count += sum(len(re.findall(p, tl)) for p in _NEUTRAL_EN)
    if neutral_count > 3:
        penalty = 5 * (neutral_count - 3)
        raw_score += penalty
        issues.append(f"[态度中立] 模糊表达 {neutral_count} 处 (+{penalty})")

    # Repeated sentence openers
    sentences = re.split(r"[。！？.!?\n]", text)
    starters = [s.strip()[:4] for s in sentences if len(s.strip()) > 8]
    for starter, cnt in Counter(starters).items():
        if cnt >= 3 and starter.strip():
            raw_score += 12
            issues.append(f"[重复开头] '{starter}' ×{cnt}")

    # Excessive list/structure markers
    list_marks = len(re.findall(r"[①②③④⑤⑥⑦⑧⑨⑩]|\b\d+\.\s|^[-•]\s", text, re.MULTILINE))
    if list_marks > 4:
        raw_score += 10
        issues.append(f"[过度结构化] {list_marks} 个列表标记")

    score = min(raw_score, 100)
    grade = "A" if score < 20 else "B" if score < 40 else "C" if score < 60 else "D"
    return {"score": score, "grade": grade, "issues": issues[:15]}
