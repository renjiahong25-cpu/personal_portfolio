# -*- coding: utf-8 -*-
"""Intent classifier quick validation"""
import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")


def main():
    from service.dah_rag.intent_classifier import IntentClassifier
    classifier = IntentClassifier()

    cases = [
        ("德国进口手机的关税税率是多少", "德国", "factual_lookup"),
        ("德国清关需要哪些步骤", "德国", "procedural_guide"),
        ("这个产品能不能进口到德国", "德国", "compliance_check"),
        ("德国和美国进口关税有什么区别", "", "comparison_analysis"),
        ("什么是报关", "", "general_inquiry"),
        ("how to clear customs in Germany", "Germany", "procedural_guide"),
        ("HS编码8517对应什么税率", "", "factual_lookup"),
        ("禁止进口的物品有哪些", "德国", "compliance_check"),
        ("美国和德国的清关流程对比", "", "comparison_analysis"),
        ("中国和德国哪个清关快", "", "comparison_analysis"),
        ("德国增值税税率是多少", "德国", "factual_lookup"),
        ("手机进口德国需要什么认证", "德国", "compliance_check"),
        ("对比美国欧盟的关税政策有何不同", "", "comparison_analysis"),
    ]

    correct = 0
    for q, c, exp in cases:
        intent = asyncio.run(classifier.classify(q, c))
        match = intent.intent_type == exp
        correct += int(match)
        mark = "PASS" if match else "FAIL"
        print(f"  [{mark}] '{q[:38]}' -> {intent.intent_type} (exp: {exp}, conf: {intent.confidence:.2f})")
    print(f"Accuracy: {correct}/{len(cases)} = {correct/len(cases):.0%}")


if __name__ == "__main__":
    main()