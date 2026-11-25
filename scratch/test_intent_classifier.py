import time
import json
from core.intent_classifier import DeterministicIntentClassifier

def run_tests():
    classifier = DeterministicIntentClassifier()
    
    test_cases = [
        ("Compare BPCL vs IOCL sales", {"intent": "COMPARE", "confidence": 0.95}),
        ("What is the market share of HPCL in Karnataka?", {"intent": "MARKET_SHARE", "confidence": 0.98}),
        ("Show MoM growth for direct sales", {"intent": "MOM", "confidence": 0.99}),
        ("Who are the top 5 companies by sales?", {"intent": "TOP_N", "confidence": 0.96}),
        ("What is the average sales of product X?", {"intent": "AVERAGE", "confidence": 0.93}),
        ("Find the decline in volume for NRL", {"intent": "DECLINE", "confidence": 0.97}),
        ("Get the total sales for GAIL", {"intent": "TOTAL_SALES", "confidence": 0.85}),
        ("Show trend of sales over time", {"intent": "TREND", "confidence": 0.94}),
        ("What is the maximum sales value in 2025?", {"intent": "MAXIMUM", "confidence": 0.93}),
        ("Rank products by contribution", {"intent": "CONTRIBUTION", "confidence": 0.98})
    ]
    
    # Warmup
    for query, _ in test_cases:
        classifier.classify(query)
        
    print("--- Running Intent Classifier Tests & Measuring Latency ---")
    
    total_time = 0.0
    for query, expected in test_cases:
        t0 = time.perf_counter()
        res = classifier.classify(query)
        dt = time.perf_counter() - t0
        total_time += dt
        
        print(f"Query: '{query}'")
        print(f"Result: {json.dumps(res)}")
        print(f"Latency: {dt * 1000:.4f} ms")
        
        # Check expected keys and values
        assert res.get("intent") == expected["intent"], f"Expected intent={expected['intent']}, got {res.get('intent')}!"
        assert res.get("confidence") == expected["confidence"], f"Expected confidence={expected['confidence']}, got {res.get('confidence')}!"
            
    avg_latency_ms = (total_time / len(test_cases)) * 1000
    print(f"\nAverage Latency: {avg_latency_ms:.4f} ms")
    assert avg_latency_ms < 1.0, f"Average latency exceeds 1ms target! ({avg_latency_ms:.4f} ms)"
    print("SUCCESS: Deterministic intent classifier is completely correct and runs way below the <1ms latency target!")

if __name__ == "__main__":
    run_tests()
