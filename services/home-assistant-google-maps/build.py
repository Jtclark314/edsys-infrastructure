"""Deterministically build the isolated frame; contains no runtime key or location."""
from pathlib import Path


def build():
    root=Path(__file__).parent
    source=(root/'renderer.js').read_text()
    if '</script' in source.lower():
        raise ValueError('Unexpected closing script in reviewed source')
    return (root/'renderer.template.html').read_text().replace('/*EDSYS_RENDERER*/',source)


if __name__=='__main__':
    Path(__file__).with_name('renderer.html').write_text(build())
