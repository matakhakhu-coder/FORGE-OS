import numpy as np


def compute_temporal_features(pulse_strengths):
    pulse_array = np.array(pulse_strengths, dtype=float)
    if pulse_array.size == 0:
        return {
            'trend': 0.0,
            'velocity': 0.0,
            'volatility': 0.0,
            'stability_index': 1.0,
        }

    if pulse_array.size == 1:
        return {
            'trend': 0.0,
            'velocity': 0.0,
            'volatility': float(pulse_array.std()),
            'stability_index': float(1.0 - pulse_array.std()),
        }

    deltas = np.diff(pulse_array)
    trend = float(np.mean(deltas))
    velocity = float(np.mean(np.abs(deltas)))
    volatility = float(np.std(pulse_array))
    stability_index = float(max(0.0, 1.0 - volatility))

    return {
        'trend': trend,
        'velocity': velocity,
        'volatility': volatility,
        'stability_index': stability_index,
    }


def classify_behavior(features):
    trend = features.get('trend', 0.0)
    velocity = features.get('velocity', 0.0)
    volatility = features.get('volatility', 0.0)

    if volatility >= 0.3 or velocity >= 0.4:
        return 'Unstable'
    if trend >= 0.05 and velocity >= 0.2:
        return 'Escalating'
    if trend < -0.05 and velocity <= 0.2:
        return 'Damping'
    return 'Steady'


def detect_patterns(pulse_strengths, event_ids=None):
    patterns = []
    if not pulse_strengths:
        return patterns

    ps = np.array(pulse_strengths, dtype=float)
    if ps.size > 1:
        if np.all(np.diff(ps) >= 0):
            patterns.append('Ascending sequence')
        if np.all(np.diff(ps) <= 0):
            patterns.append('Descending sequence')

    peaks = np.where((ps[1:-1] > ps[:-2]) & (ps[1:-1] > ps[2:]))[0]
    if peaks.size >= 2:
        patterns.append('Multiple peaks')

    if event_ids:
        unique_events = len(set(event_ids))
        if unique_events >= 3:
            patterns.append('Diverse event types')

    return patterns
