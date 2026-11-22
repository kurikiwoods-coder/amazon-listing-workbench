import re

from .models import PUBLISHED_COPY, Question

GENERAL = [
    ("What is this product and what does it do?", "确认产品身份和主要用途"),
    ("What is included in the box?", "确认是否需要额外购买配件"),
    ("What are its dimensions?", "确认大小和摆放空间"),
    ("What are the capacity or performance limits?", "了解参数和使用上限"),
    ("What materials is it made from?", "核对材质或接触面"),
    ("How do I set it up for the first time?", "明确首次设置步骤"),
    ("How do I use it day to day?", "确认日常操作"),
    ("What does it work with?", "确认兼容条件"),
    ("When should I avoid using it?", "了解限制和禁用场景"),
    ("How should I clean or maintain it?", "明确清洁和维护方式"),
    ("Does it require power, batteries or charging?", "确认供电要求"),
    ("What makes it useful for my intended use?", "核对实际使用场景"),
]
SCALE = [
    ("Can I see calories and protein on the scale without reopening the app?",
     "区分首次设置与日常称量时是否打开 App"),
    ("How do I save foods before using the scale?", "明确预设食物的操作路径"),
    ("Can I look up a food that I haven't saved?", "确认非预设食物的入口"),
    ("What weight range and increments does the scale support?", "确认最小称量、最大称量和分度值"),
    ("Can I subtract the weight of a bowl?", "确认去皮功能"),
    ("Which units can I use?", "确认切换单位与换算"),
    ("Can I use the scale for repeat meal prep?", "确认重复备餐的适用性"),
    ("Can I log meals or scan a barcode on the scale itself?", "明确扫码、记账与秤端的边界"),
    ("How should I clean the weighing surface?", "确认清洁和防水限制"),
    ("What powers the scale and what is in the box?", "核对供电和随机附件"),
    ("What happens when I put a food on the scale without selecting it?", "核对是否自动识别食物"),
    ("Does this scale support every food on the device?", "核对只能选择已保存食物的边界"),
]
SCALE_ANCHORS = [
    ["calories and protein", "without reopening the app", "saved food"],
    ["use the app", "save foods"], ["look up", "new foods"], ["weigh from", "increments"],
    ["tare", "subtract"], ["grams", "ounces"], ["meal prep", "saved foods"],
    ["scan a barcode", "log meals"], ["wipe", "clean"], ["batteries", "included"],
    ["select a saved food"], ["saved food", "look up"],
]
GENERAL_ANCHORS = [
    [], ["in the box"], ["dimensions"], ["capacity"], ["made of"], ["set up"],
    ["to use"], ["compatible with"], ["use only"], ["clean"], ["batteries"], ["designed for"],
]
CATEGORY_QUESTIONS = [
    (r"\b(?:chargers?|charging|adapters?)\b|电源|充电", [
        ("Which devices and charging protocols are supported?", "核对设备及协议兼容性"),
        ("What is the output when several ports are in use?", "核对多口同时使用的功率"),
        ("Is a charging cable included?", "确认线材是否随附"),
    ]),
    (r"\b(?:bottles?|tumblers?)\b|水杯|水瓶", [
        ("Does the lid seal when the bottle is inverted?", "确认倒置时的防漏边界"),
        ("Can I use it with hot drinks?", "核对温度限制"),
        ("Can the bottle and lid go in a dishwasher?", "核对两处的清洁方式"),
    ]),
    (r"\b(?:lamps?|lanterns?|lighting)\b|灯", [
        ("How long does it run at the highest brightness?", "核对最大亮度时的续航"),
        ("Can I use it outdoors in rain?", "核对防护等级"),
        ("Can I adjust brightness or color temperature?", "核对灯光档位和色温"),
    ]),
]


def template_questions(product: dict) -> list[dict]:
    category = product["category"].strip() or product["name"]
    if re.search(r"\b(?:food|nutrition|kitchen|food weighing) scale\b|营养秤|食物[秤称]|厨房秤", category, re.I):
        rows, anchors = SCALE, SCALE_ANCHORS
    else:
        specific = next((items for pattern, items in CATEGORY_QUESTIONS if re.search(pattern, category, re.I)), [])
        rows = [*specific, *GENERAL]
        anchors = [[] for _ in specific] + GENERAL_ANCHORS
    return [
        Question(id=f"q-{i + 1:02}", text=text, intent=intent,
                 priority="high" if i < 3 else "standard", source="template", anchors=anchors[i],
                 fields=list(PUBLISHED_COPY)).model_dump()
        for i, (text, intent) in enumerate(rows)
    ]
