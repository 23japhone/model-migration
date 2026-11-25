"""Verify full-vocabulary probabilities captured immediately before random_sample."""
import math


def decision(scores, probabilities, candidate):
    if len(scores) != len(probabilities) or not 0 <= candidate < len(scores):
        raise ValueError('invalid sample row or vocabulary')
    top = max(range(len(scores)), key=scores.__getitem__)
    peak = scores[top]
    if not math.isfinite(peak) or any(not math.isfinite(p) or p < 0 for p in probabilities):
        raise ValueError('invalid probability distribution')
    weights = [math.exp(x - peak) for x in scores]
    total = math.fsum(weights)
    if any(not math.isclose(p, x / total, rel_tol=2e-5, abs_tol=1e-8)
           for x, p in zip(weights, probabilities)) or not math.isclose(math.fsum(probabilities), 1, abs_tol=1e-5):
        raise ValueError('observed probabilities differ from filtered scores')
    if not probabilities[candidate] > 0:
        raise ValueError('sampled token outside effective support')
    return {'strategy': 'seeded_random', 'argmax': top, 'candidate': candidate, 'is_argmax': top == candidate,
            'selected_probability': probabilities[candidate], 'support_size': sum(p > 0 for p in probabilities)}
