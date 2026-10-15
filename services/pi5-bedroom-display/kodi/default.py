"""Manual Kodi GUI spike. Not installed or enabled by the build command."""
import json
from pathlib import Path
import time

import xbmc
import xbmcgui

from model import View, kodi_text
from character import Character,frame_path,pose_for_view

ROOT = Path(__file__).resolve().parent


class Overlay(xbmcgui.WindowDialog):
    def __init__(self, view):
        super().__init__()
        self.view = view
        self.done = False
        self.until = time.monotonic() + view.ttl_seconds
        w, h = self.getWidth(), self.getHeight()

        def rect(x, y, width, height):
            return [round(x*w/1920), round(y*h/1080), round(width*w/1920), round(height*h/1080)]

        panel = xbmcgui.ControlImage(*rect(620, 570, 1180, 410), str(ROOT/'assets/panel.png'))
        pose=pose_for_view(view.state,view.speaking)
        self.avatar = xbmcgui.ControlImage(*rect(120, 390, 470, 627), str(frame_path(ROOT,pose,0)), aspectRatio=2)
        self.character=Character(self.avatar,ROOT,pose)
        label = xbmcgui.ControlLabel(*rect(670, 605, 1080, 45), 'SYNTHETIC DEMO  /  '+view.state.upper(), font='font13', textColor='FFD4C5A3')
        heading = xbmcgui.ControlLabel(*rect(670, 655, 1080, 60), kodi_text(view.heading), font='font30', textColor='FFF4F0E7')
        body = xbmcgui.ControlTextBox(*rect(670, 730, 1060, 200), font='font13', textColor='FFCFDCD6')
        self.addControls([panel, self.avatar, label, heading, body])
        body.setText(kodi_text(view.body+'\n\n'+'\n'.join(view.items)))

    def onAction(self, action):
        # Back, stop, previous-menu and select close only this overlay.
        # They are not forwarded as player commands.
        if action.getId() in (7, 9, 10, 13, 92):
            self.done = True

    def tick(self):
        self.character.tick()


def main():
    views = [View.from_dict(v) for v in json.loads((ROOT/'views.json').read_text(encoding='utf-8'))]
    selected = xbmcgui.Dialog().select('Synthetic bedroom display demo', [v.key for v in views])
    if selected < 0:
        return
    dialog = Overlay(views[selected])
    monitor = xbmc.Monitor()
    dialog.show()
    try:
        while not dialog.done and time.monotonic() < dialog.until:
            dialog.tick()
            if monitor.waitForAbort(0.025):
                break
    finally:
        dialog.close()
        del dialog


if __name__ == '__main__':
    main()
