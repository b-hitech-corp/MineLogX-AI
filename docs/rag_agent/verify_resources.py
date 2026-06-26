import requests
import json
import time

# ================================================================
# ENDPOINTS
# ================================================================
QWEN3_ENDPOINT      = "http://ec2-98-81-228-187.compute-1.amazonaws.com:11434"
GEMMA3_ENDPOINT     = "http://ec2-100-31-82-64.compute-1.amazonaws.com:11434"
EMBEDDINGS_ENDPOINT = "http://ec2-3-208-23-94.compute-1.amazonaws.com:11434"

# ================================================================
# HELPERS
# ================================================================
def query_model(endpoint, model, prompt):
    """Query a chat model and return response + time"""
    try:
        start = time.time()
        response = requests.post(
            f"{endpoint}/api/generate",
            json={
                "model": model,
                "prompt": prompt,
                "stream": False
            },
            timeout=120
        )
        elapsed = time.time() - start
        result = response.json()
        return result.get("response", "No response"), elapsed, None
    except Exception as e:
        return None, 0, str(e)

def get_embeddings(endpoint, model, text):
    """Get embeddings for a text and return vector + time"""
    try:
        start = time.time()
        response = requests.post(
            f"{endpoint}/api/embeddings",
            json={
                "model": model,
                "prompt": text
            },
            timeout=30
        )
        elapsed = time.time() - start
        result = response.json()
        embedding = result.get("embedding", [])
        return embedding, elapsed, None
    except Exception as e:
        return None, 0, str(e)

def check_health(endpoint, model):
    """Check if a model is available"""
    try:
        response = requests.get(f"{endpoint}/api/tags", timeout=10)
        models = response.json().get("models", [])
        available = [m["name"] for m in models]
        return model in available or any(model in m for m in available)
    except Exception as e:
        return False

# ================================================================
# TESTS
# ================================================================
def test_health_checks():
    print("\n" + "=" * 60)
    print("🔍 HEALTH CHECKS")
    print("=" * 60)

    checks = [
        (QWEN3_ENDPOINT,      "qwen3:8b"),
        (GEMMA3_ENDPOINT,     "gemma3:12b"),
        (EMBEDDINGS_ENDPOINT, "mxbai-embed-large"),
    ]

    for endpoint, model in checks:
        status = check_health(endpoint, model)
        icon = "✅" if status else "❌"
        print(f"{icon} {model} — {endpoint}")

def test_chat_models():
    print("\n" + "=" * 60)
    print("🤖 CHAT MODELS TEST")
    print("=" * 60)

    prompt = "In one sentence, what is fleet telemetry in mining operations?"

    models = [
        (QWEN3_ENDPOINT,  "qwen3:8b",   "Qwen3 8B"),
        (GEMMA3_ENDPOINT, "gemma3:12b", "Gemma3 12B"),
    ]

    for endpoint, model, name in models:
        print(f"\n📤 Prompt: {prompt}")
        print(f"🤖 Model: {name}")
        response, elapsed, error = query_model(endpoint, model, prompt)
        if error:
            print(f"❌ Error: {error}")
        else:
            print(f"📥 Response: {response}")
            print(f"⏱️  Time: {elapsed:.2f}s")

def test_embeddings():
    print("\n" + "=" * 60)
    print("🔢 EMBEDDINGS TEST")
    print("=" * 60)

    texts = [
        "Mining fleet telemetry analysis",
        "Fuel consumption anomaly detection",
        "Fatigue monitoring for truck drivers",
    ]

    for text in texts:
        print(f"\n📤 Text: {text}")
        embedding, elapsed, error = get_embeddings(
            EMBEDDINGS_ENDPOINT, "mxbai-embed-large", text
        )
        if error:
            print(f"❌ Error: {error}")
        else:
            print(f"📐 Dimensions: {len(embedding)}")
            print(f"🔢 First 5 values: {embedding[:5]}")
            print(f"⏱️  Time: {elapsed:.2f}s")

def test_similarity():
    print("\n" + "=" * 60)
    print("🔍 SIMILARITY TEST")
    print("=" * 60)

    import math

    def cosine_similarity(v1, v2):
        dot = sum(a * b for a, b in zip(v1, v2))
        mag1 = math.sqrt(sum(a ** 2 for a in v1))
        mag2 = math.sqrt(sum(b ** 2 for b in v2))
        return dot / (mag1 * mag2) if mag1 and mag2 else 0

    texts = [
        "Truck fuel consumption is abnormally high",
        "Vehicle is using too much fuel",
        "The weather is sunny today",
    ]

    embeddings = []
    for text in texts:
        emb, _, error = get_embeddings(
            EMBEDDINGS_ENDPOINT, "mxbai-embed-large", text
        )
        if not error:
            embeddings.append((text, emb))

    if len(embeddings) >= 2:
        base_text, base_emb = embeddings[0]
        print(f"\n📌 Base: '{base_text}'")
        for text, emb in embeddings[1:]:
            similarity = cosine_similarity(base_emb, emb)
            print(f"🔗 vs '{text}' → similarity: {similarity:.4f}")

# ================================================================
# MAIN
# ================================================================
if __name__ == "__main__":
    print("\n🚀 MineLogX AI - Model Tests")
    print("=" * 60)

    test_health_checks()
    test_embeddings()
    test_similarity()
    test_chat_models()

    print("\n" + "=" * 60)
    print("✅ Tests completed!")
    print("=" * 60)