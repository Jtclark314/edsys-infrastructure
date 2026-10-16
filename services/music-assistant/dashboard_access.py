#!/usr/bin/env python3
"""Native Music Assistant launchers; compare live dashboards before narrow saves."""
import argparse
import copy
import json
from pathlib import Path
import sys

MUSIC_PATH = '/app/d5369777_music_assistant'
LEGACY_MUSIC_PATH = '/hassio/ingress/d5369777_music_assistant'
DASHBOARDS = ('edsys-home', 'edsys-spaces', 'edsys-media', 'edsys-pi')
# Explicit existing view/section targets. No unrelated views are rebuilt.
TARGETS = {
    'edsys-home': {'home': ['Now Playing']},
    'edsys-spaces': {'inside': ['Living Room', 'Movie Room', 'Master Bedroom', 'Secondary Bedroom']},
    'edsys-media': {'media': ['Music & Scenes'], 'movie-room': ['Playback'],
                    'living-room': ['Playback'], 'master-bedroom': ['Master Bedroom remote'],
                    'bedroom-pi': ['Now playing on the Pi']},
    'edsys-pi': {'player': ['Now playing on the Pi'], 'music-books': ['Choose something to play'],
                 'night': ['Sleep timer'], 'lg-remote': ['Master Bedroom remote']},
}


def shortcut():
    return {'type': 'shortcut', 'label': 'Music Assistant',
            'description': 'Music & audiobooks', 'icon': 'mdi:music-circle', 'color': 'teal',
            'tap_action': {'action': 'navigate', 'navigation_path': MUSIC_PATH},
            'hold_action': {'action': 'none'}, 'double_tap_action': {'action': 'none'},
            'grid_options': {'columns': 12, 'rows': 1}}


def is_music_launcher(card):
    action = card.get('tap_action', {})
    return (action.get('action') == 'navigate' and action.get('navigation_path') in {MUSIC_PATH, LEGACY_MUSIC_PATH}
            or action.get('action') == 'url'
            and action.get('url_path', '').rstrip('/') == 'http://192.168.50.75:8095')


def extend(original, url_path):
    result = copy.deepcopy(original)
    placements = []
    for path, headings in TARGETS[url_path].items():
        views = [v for v in result['views'] if v.get('path') == path]
        if len(views) != 1:
            raise ValueError('Expected one reviewed view: ' + path)
        for title in headings:
            sections = [s for s in views[0]['sections'] if any(
                c.get('type') == 'heading' and c.get('heading') == title for c in s['cards'])]
            if len(sections) != 1:
                raise ValueError('Expected one reviewed section: ' + title)
            cards = sections[0]['cards']
            existing = [i for i, c in enumerate(cards) if is_music_launcher(c)]
            if len(existing) > 1:
                raise ValueError('Duplicate Music launchers; inspect before changing')
            if existing:
                cards[existing[0]] = shortcut()
                operation = 'replace launcher'
            else:
                insert = next(i for i, c in enumerate(cards) if c.get('type') == 'heading') + 1
                cards.insert(insert, shortcut())
                operation = 'insert launcher'
            if url_path == 'edsys-pi' and path == 'music-books':
                for c in cards:
                    if c.get('type') == 'markdown' and c.get('content', '').startswith('In Music Assistant, choose **Bedroom Pi Plex**'):
                        c['content'] = c['content'].replace(
                            'choose **Bedroom Pi Plex** as the output,',
                            'click the current device name at the **bottom right** to open the player picker and choose **Bedroom Pi Plex** as the output,')
            placements.append({'view': path, 'section': title, 'operation': operation})
    return result, placements


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--private-state', required=True, type=Path)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'home-assistant-voice'))
    from configure import client, ws
    api = client()
    panels = ws(api, {'type': 'get_panels'})
    if panels.get('app', {}).get('component_name') != 'app':
        raise RuntimeError('Native app panel is not registered; inspect the current frontend route')
    args.private_state.mkdir(parents=True, exist_ok=True, mode=0o700)
    candidates = {}
    for url_path in DASHBOARDS:
        before = ws(api, {'type': 'lovelace/config', 'url_path': url_path})
        after, placements = extend(before, url_path)
        candidates[url_path] = (before, after)
        for suffix, data in [('before', before), ('after', after)]:
            target = args.private_state / (url_path + '-' + suffix + '.json')
            if target.exists() and json.loads(target.read_text()) != data:
                raise RuntimeError('Private checkpoint differs; inspect instead of replaying')
            target.write_text(json.dumps(data, indent=2) + '\n'); target.chmod(0o600)
        print(json.dumps({'dashboard': url_path, 'placements': placements}), flush=True)
    if not args.apply:
        return
    for url_path, (before, _) in candidates.items():
        if ws(api, {'type': 'lovelace/config', 'url_path': url_path}) != before:
            raise RuntimeError('Concurrent edit; no writes issued')
    for url_path, (before, after) in candidates.items():
        if before == after:
            continue
        if ws(api, {'type': 'lovelace/config', 'url_path': url_path}) != before:
            raise RuntimeError('Concurrent edit; inspect any already-saved dashboard before continuing')
        ws(api, {'type': 'lovelace/config/save', 'url_path': url_path, 'config': after})
        if ws(api, {'type': 'lovelace/config', 'url_path': url_path}) != after:
            raise RuntimeError('Readback differs; inspect without replay')
        print(json.dumps({'dashboard': url_path, 'exact_readback': True}), flush=True)


if __name__ == '__main__':
    main()
