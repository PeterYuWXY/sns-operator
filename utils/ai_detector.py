"""Detect AI-generated tone patterns in text. Score 0-100 (0 = human, 100 = obvious AI)."""
import re
from collections import Counter
from typing import Dict, List

# Chinese AI writing clichés
_CN = [
    r"值得注意的是", r"不容忽视", r"毋庸置疑", r"总的来说", r"综上所述",
    r"不得不说", r"显而易见", r"众所周知", r"与此同时", r"在此背景下",
    r"从某种程度上说", r"不言而喻", r"从这个角度来看", r"从长远来看",
    r"在某种意义上", r"在这种情况下", r"针对这一问题", r"就目前而言",
    r"换句话说", r"总而言之", r"简而言之", r"深度.*分析", r"全面.*解析",
    r"深度.*解读", r"一文.*读懂", r"让我们", r"本文.*", r"笔者.*",
    r"值得一提的是", r"不可否认", r"从全局来看", r"在此基础上",
    r"进一步来说", r"除此之外", r"值得关注的是", r"尤其值得注意",
    r"毫无疑问", r"可以预见", r"不难发现", r"这也意味着",
    r"不难看出", r"由此可见", r"由此可知", r"不禁让人", r"引人深思",
    r"令人深思", r"发人深省", r"值得深思", r"不得不.*思考",
    r"对此.*看法", r"笔者认为", r"在笔者看来", r"作者认为",
    r"随着.*发展", r"随着.*时代", r"随着.*进步", r"随着.*普及",
    r"当今社会", r"在当今", r"当下.*时代", r"新时代.*背景",
    r"在这个.*时代", r"如今.*时代", r"面对.*挑战", r"面临.*机遇",
    r"如何.*成为", r"如何.*实现", r"如何.*做到", r"如何.*应对",
    r"背后的.*逻辑", r"背后的.*原因", r"背后的.*真相",
    r"深层.*原因", r"根本.*原因", r"核心.*问题", r"关键.*所在",
    r"重要.*意义", r"深远.*影响", r"巨大.*影响", r"不可.*忽视",
    r"值得.*期待", r"令人.*期待", r"引人.*关注",
    r"一、", r"二、", r"三、", r"（一）", r"（二）", r"（三）",
    r"首先.*其次.*最后", r"第一.*第二.*第三",
]

# English AI writing patterns
_EN = [
    r"it'?s worth noting", r"it is worth noting", r"needless to say",
    r"in conclusion", r"in summary", r"to summarize", r"to conclude",
    r"furthermore", r"moreover", r"additionally", r"in addition",
    r"it'?s important to note", r"it should be noted", r"notably",
    r"undoubtedly", r"without a doubt", r"there'?s no denying",
    r"as we all know", r"as everyone knows", r"it goes without saying",
    r"let me", r"let'?s explore", r"let'?s dive", r"let'?s take a look",
    r"in this article", r"in this post", r"today we will", r"in today'?s",
    r"the bottom line", r"at the end of the day", r"when all is said and done",
    r"all things considered", r"in light of", r"it'?s clear that",
    r"it is clear that", r"by and large", r"on the other hand",
    r"on the flip side", r"in contrast", r"nevertheless", r"nonetheless",
    r"having said that", r"that being said", r"in any case",
    r"generally speaking", r"broadly speaking", r"in other words",
    r"as a result", r"as such", r"to be fair", r"to be honest",
    r"the fact of the matter", r"the reality is", r"the truth is",
    r"it'?s no secret", r"it comes as no surprise", r"unsurprisingly",
    r"interestingly", r"importantly", r"significantly", r"remarkably",
    r"first(ly)?.*second(ly)?.*third(ly)?", r"firstly.*secondly.*lastly",
    r"in the first place", r"last but not least",
    r"dive deep", r"deep dive", r"unpack", r"delve into",
    r"game.?changer", r"paradigm shift", r"move the needle",
    r"at its core", r"at the heart of", r"at the end of the day",
]


def detect(text: str) -> Dict:
    issues: List[str] = []
    raw_score = 0

    for p in _CN:
        if re.search(p, text, re.IGNORECASE):
            raw_score += 7
            issues.append(f"[CN] {p}")

    tl = text.lower()
    for p in _EN:
        if re.search(p, tl):
            raw_score += 7
            issues.append(f"[EN] {p}")

    # Repeated sentence openers
    sentences = re.split(r"[。！？.!?\n]", text)
    starters = [s.strip()[:4] for s in sentences if len(s.strip()) > 8]
    for starter, cnt in Counter(starters).items():
        if cnt >= 3 and starter.strip():
            raw_score += 12
            issues.append(f"[REPEAT] '{starter}' ×{cnt}")

    # Excessive list structure
    list_marks = len(re.findall(r"[①②③④⑤⑥⑦⑧⑨⑩]|\b\d+\.\s|^[-•]\s", text, re.MULTILINE))
    if list_marks > 4:
        raw_score += 10
        issues.append(f"[STRUCT] {list_marks} list markers")

    score = min(raw_score, 100)
    grade = "A" if score < 20 else "B" if score < 40 else "C" if score < 60 else "D"
    return {"score": score, "grade": grade, "issues": issues[:12]}
