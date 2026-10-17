"""Read preview metadata without modifying the original ebook."""
import os
import zipfile
from xml.etree import ElementTree as ET


def read_preview_metadata(path):
    ext = os.path.splitext(path)[1].lower()
    result = {}
    if ext == '.cbz':
        with zipfile.ZipFile(path) as z:
            name = next((x for x in z.namelist() if x.rsplit('/', 1)[-1].lower() == 'comicinfo.xml'), None)
            if not name:
                return result
            root = ET.fromstring(z.read(name))
            def value(key):
                element = next((node for node in root if node.tag.rsplit('}', 1)[-1].lower() == key.lower()), None)
                return (element.text or '').strip() if element is not None else ''
            result['title'] = value('Title')
            result['series'] = value('Series')
            result['publisher'] = value('Publisher')
            result['tags'] = ', '.join(x for x in (value('Genre'), value('Tags')) if x)
            writers = [x.strip() for key in ('Writer', 'Penciller') for x in value(key).split(',') if x.strip()]
            result['authors'] = list(dict.fromkeys(writers))
            num = value('Number')
            if num:
                try:
                    result['number'] = float(num)
                except ValueError:
                    pass
        return result
    if ext == '.epub':
        from calibre.ebooks.metadata.meta import get_metadata
        with open(path, 'rb') as stream:
            mi = get_metadata(stream, 'epub', force_read_metadata=True)
        result['title'] = mi.title or ''
        result['series'] = mi.series or ''
        result['authors'] = [x for x in (mi.authors or []) if x and x != 'Unknown']
        result['publisher'] = mi.publisher or ''
        result['tags'] = ', '.join(mi.tags or [])
        if mi.series:
            result['number'] = mi.series_index
    return result
