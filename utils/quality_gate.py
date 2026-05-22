"""Content quality scoring. Returns 0-100 (higher = better). Blocks below threshold."""
import re
from typing import Dict, List
from utils.ai_detector import detect as detect_ai


def score(text: str, platform: str = "x") -> Dict:
    result = {"text": text, "platform": platform, "checks": {}, "suggestions": []}

    # 1. AI tone (lower raw score is better; penalise above 20)
    ai = detect_ai(text)
    result["checks"]["ai_tone"] = ai
    ai_penalty = max(0, ai["score"] - 20) * 0.6  # 0-48 penalty

    # 2. Length check
    char_count = len(text)
    if platform == "x":
        optimal = (180, 260)
        hard_limit = 280
    else:
        optimal = (100, 500)
        hard_limit = 2000

    length_score = 10
    if char_count > hard_limit:
        length_score = -20
        result["suggestions"].append(f"Too long ({char_count} chars). Hard limit: {hard_limit}.")
    elif char_count < optimal[0]:
        length_score = 0
        result["suggestions"].append(f"Too short ({char_count} chars). Aim for {optimal[0]}+.")
    result["checks"]["length"] = {"chars": char_count, "score": length_score}

    # 3. Hashtag count
    hashtags = re.findall(r"#\w+", text)
    hashtag_penalty = max(0, len(hashtags) - 3) * 5
    if hashtag_penalty:
        result["suggestions"].append(f"Too many hashtags ({len(hashtags)}). Keep ≤3.")
    result["checks"]["hashtags"] = {"count": len(hashtags), "penalty": hashtag_penalty}

    # 4. Engagement hooks (+bonus)
    bonus = 0
    if re.search(r"[？?]", text):
        bonus += 6
        result["checks"]["has_question"] = True
    if re.search(r"\d+[%倍x×]|\$\d|\d+[万亿B]", text):
        bonus += 5
        result["checks"]["has_data"] = True
    if re.search(r"\b(我|我们|你|你们|I|we|you)\b", text, re.IGNORECASE):
        bonus += 4
        result["checks"]["has_personal_voice"] = True

    # 5. Forbidden filler words
    filler = re.findall(
        r"非常好|很好|很棒|太棒了|绝了|厉害了|awesome|amazing|incredible|game.changer", text, re.IGNORECASE
    )
    filler_penalty = len(filler) * 5
    if filler:
        result["suggestions"].append(f"Replace generic filler: {filler[:3]}")
    result["checks"]["filler"] = {"found": filler, "penalty": filler_penalty}

    # Final score
    raw = 100 - ai_penalty - hashtag_penalty - filler_penalty + length_score + bonus
    final = max(0, min(100, round(raw)))
    grade = "A" if final >= 85 else "B" if final >= 70 else "C" if final >= 55 else "D"

    result["score"] = final
    result["grade"] = grade
    result["pass"] = final >= 70 and ai["score"] < 50
    return result


def batch_score(texts: List[str], platform: str = "x") -> List[Dict]:
    return [score(t, platform) for t in texts]
