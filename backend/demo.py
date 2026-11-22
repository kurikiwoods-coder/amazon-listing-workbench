from .audit import ENGINE_VERSION, score_listing
from .models import ContextInput, ListingInput, ProductInput
from .questions import template_questions
from .rules import rules_for

DEMO_ASIN = "B0DEMO1001"
PRODUCT = {
    "asin": DEMO_ASIN, "name": "Evermeasure 智能营养秤", "sku": "EM-SCALE-DEMO",
    "market": "US", "brand": "Evermeasure", "category": "Kitchen & Dining · Food Scales", "product_type": "non_media",
}
DEMO_SOURCE = "演示样本 · 模拟产品说明"
LISTING = {
    "title": "Evermeasure Smart Nutrition Food Scale for Kitchen, Meal Prep and Daily Calorie Tracking",
    "highlights": "A smart food scale for kitchen meal prep, portion control and everyday nutrition tracking.",
    "bullets": "\n".join([
        "View Calories and Protein · All features work without the app",
        "Capacity: Weigh from 3 g to 5,000 g in 1 g increments",
        "Tare: Subtract bowl weight before weighing ingredients",
        "Precision Portion Control · Premium quality and easy to use",
        "Get Nutrition Details · Use the app to look up new foods",
    ]),
    "aplus": "An everyday food scale for your kitchen.\nMade for breakfast prep and portion control.",
    "attributes": "Capacity: 5,000 g\nUnits: g, oz\nDisplay: Color screen",
    "keywords": "nutrition food scale, portion control, color display, saved foods, tare",
    "reviews": "模拟问句：Do I need to open my phone app each morning?",
    "qa": "模拟问句：How do I select my saved food on the scale?",
}
FACTS = [
    ("f-01", "商品身份", "Evermeasure Smart Nutrition Food Scale", "identity", []),
    ("f-02", "已保存食物可在秤端看热量和蛋白质",
     "Select a saved food on the scale to see calories and protein without reopening the app", "feature", []),
    ("f-03", "首次预设需要 App", "Use the app for initial setup and to save foods before weighing", "boundary",
     ["All features work without the app"]),
    ("f-04", "称量范围和分度", "Capacity: Weigh from 3 g to 5,000 g in 1 g increments", "spec", []),
    ("f-05", "去皮", "Tare: Subtract bowl weight before weighing ingredients", "feature", []),
    ("f-06", "非预设食物用 App 查询", "Use the app to look up new foods", "boundary", []),
    ("f-07", "早餐和重复备餐", "Use saved foods for repeat meal prep and breakfast portion control", "use", []),
    ("f-08", "彩屏显示预设食物的营养数据", "The color display shows nutrition for saved foods as you weigh", "difference", []),
    ("f-09", "重量单位", "Choose grams or ounces for everyday weighing", "spec", []),
    ("f-10", "彩屏", "Color Display for Saved Foods", "difference", []),
]
LINKS = {
    "q-01": (["calories and protein", "without reopening the app", "saved food"], ["f-02", "f-03"]),
    "q-02": (["use the app", "to save foods"], ["f-03"]),
    "q-03": (["look up new foods"], ["f-06"]),
    "q-04": (["3 g to 5,000 g", "1 g increments"], ["f-04"]),
    "q-05": (["tare", "subtract bowl weight"], ["f-05"]),
    "q-06": (["grams or ounces"], ["f-09"]),
    "q-07": (["meal prep", "saved foods"], ["f-07"]),
    "q-08": (["scan a barcode", "log meals"], []),
    "q-09": (["wipe", "clean"], []),
    "q-10": (["batteries", "included"], []),
    "q-11": (["select a saved food"], ["f-02"]),
    "q-12": (["saved food", "look up new foods"], ["f-02", "f-06"]),
}


def seed_demo(db, automatic=False) -> dict | None:
    item = ProductInput.model_validate(PRODUCT).model_dump()
    with db.connect(write=True) as conn:
        if automatic and conn.execute("SELECT 1 FROM workbench_settings WHERE key='demo_dismissed'").fetchone():
            return None
        product = db.create_product(conn, item, demo=True)
        current = db.snapshot(conn, product["id"])
        if current["version"] != 1 or any(row["state"] != "missing" for row in current["fields"].values()):
            return product
        fields = ListingInput(fields={
            key: {"state": "confirmed", "text": value, "source": DEMO_SOURCE}
            for key, value in LISTING.items()
        }).normalized()
        snapshot = db.new_snapshot(conn, product["id"], fields, "示例文案，用于体验评分与改写。", "demo")
        facts = [{"id": fid, "label": label, "copy": copy, "source": DEMO_SOURCE, "state": "confirmed",
                  "role": role, "conflicts": conflicts} for fid, label, copy, role, conflicts in FACTS]
        questions = template_questions(product)
        for question in questions:
            anchors, fact_ids = LINKS[question["id"]]
            question.update(anchors=anchors, fact_ids=fact_ids, fields=["title", "highlights", "bullets", "aplus"])
        questions[0].update(source="alexa")
        data = ContextInput(facts=facts, questions=questions).model_dump(by_alias=True)
        context = db.new_context(conn, product["id"], data)
        rules = rules_for(product)
        result = score_listing(product, fields, data, rules)
        db.new_audit(conn, product["id"], snapshot["id"], context["id"], "audit", f"demo-{product['id']}",
                     {"result": result, "rules": rules, "mode": "rules", "model": None,
                      "engine_version": ENGINE_VERSION, "llm": None, "product": product})
        return product
