"""Canonical identities for the two NFL channels shared by stream providers."""
import re


def channel_identity(game):
    text = game.get('title') or (game.get('slug') or '').replace('-', ' ')
    text = re.sub(r'^live\s+vs\.?\s+|\s+vs\.?\s+live$', '', text.strip(), flags=re.I).strip()
    if re.fullmatch(r'(?:nfl\s*)?red\s*zone', text, re.I):
        return 'nfl-redzone', 'NFL RedZone'
    if re.fullmatch(r'nfl\s*network', text, re.I):
        return 'nfl-network', 'NFL Network'
    return None


def merge_channels(games):
    """Coalesce cross-provider channel rows, preserving unique stream options.

    Keep the first entry's id/uid so existing selections stay stable. Ordinary
    matchups are untouched; this cannot collapse separate scheduled fixtures.
    """
    result = []
    channels = {}
    for game in games:
        identity = channel_identity(game)
        if identity is None:
            result.append(game)
            continue
        key, label = identity
        if key not in channels:
            target = dict(game)
            target.update(title=label + ' vs Live', display_title=label,
                          away_team=label, home_team='Live', always_live=False,
                          is_matchup=False, streams=[], stream_sources=[], all_wrapper_count=0)
            channels[key] = target
            result.append(target)
        target = channels[key]
        streams = {s['media_url']: s for s in target['streams'] if s.get('media_url')}
        for stream in game.get('streams') or []:
            url = stream.get('media_url')
            if url and (url not in streams or (streams[url].get('stale') and not stream.get('stale'))):
                streams[url] = stream
        target['streams'] = list(streams.values())
        sources = set(target['stream_sources']) | set(game.get('stream_sources') or [])
        if game.get('source') and game.get('streams'):
            sources.add(game['source'])
        target['stream_sources'] = sorted(sources)
        target['all_wrapper_count'] += game.get('all_wrapper_count') or 0
        target['stream_count'] = len(target['streams'])
        target['resolved_count'] = sum(not s.get('stale', False) for s in target['streams'])
        target['stale'] = bool(target['streams']) and not target['resolved_count']
    return result
