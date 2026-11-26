"""Split existing playback fixtures into the two provider contracts."""


def split_performance(data):
    if isinstance(data, Exception):
        return data, data
    drawing = {key: data[key] for key in ('version', 'status', 'reason', 'label', 'parts')}
    drawing['version'] = 2
    drawing['parts'] = [
        {**p, 'label': {'ru': 'часть рисунка', 'en': 'drawing part'}, 'pivot_type': 'center', 'kind': 'part'}
        for p in drawing['parts']
    ]
    motion = {key: data[key] for key in ('version', 'status', 'reason', 'duration', 'tracks', 'repeats')}
    return drawing, motion
