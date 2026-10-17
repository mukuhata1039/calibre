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
            result['subtitle'] = value('Subtitle')
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
        # calibre's main Metadata object does not expose the EPUB 3 subtitle.
        # Read it separately from the package document, without editing EPUB.
        try:
            with zipfile.ZipFile(path) as archive:
                container = ET.fromstring(archive.read('META-INF/container.xml'))
                rootfile = next((x for x in container.iter()
                                 if x.tag.rsplit('}', 1)[-1] == 'rootfile'), None)
                opf_name = rootfile.get('full-path') if rootfile is not None else None
                if opf_name:
                    package = ET.fromstring(archive.read(opf_name))
                    nodes = list(package.iter())
                    subtitle_ids = {
                        (x.get('refines') or '').lstrip('#')
                        for x in nodes if x.tag.rsplit('}', 1)[-1] == 'meta'
                        and x.get('property') == 'title-type'
                        and (x.text or '').strip() == 'subtitle'
                    }
                    subtitles = [
                        (x.text or '').strip() for x in nodes
                        if x.tag.rsplit('}', 1)[-1] == 'title'
                        and x.get('id') in subtitle_ids
                    ]
                    if not subtitles:
                        subtitles = [
                            (x.get('content') or '').strip() for x in nodes
                            if x.tag.rsplit('}', 1)[-1] == 'meta'
                            and x.get('name', '').lower() == 'subtitle'
                        ]
                    result['subtitle'] = next((x for x in subtitles if x), '')
        except (OSError, KeyError, ValueError, ET.ParseError, zipfile.BadZipFile):
            pass
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


BOOK_EXTENSIONS = frozenset({
    '.epub', '.cbz', '.cbr', '.pdf', '.azw', '.azw3', '.mobi',
    '.txt', '.fb2', '.djvu', '.docx', '.rtf', '.html', '.htm',
    '.kepub', '.kfx',
})


def expand_book_paths(paths):
    """Recursively expand local folders, ignoring non-books and duplicates."""
    result = []
    seen = set()

    def add(path):
        normalized = os.path.normcase(os.path.abspath(path))
        if normalized not in seen and os.path.isfile(path):
            seen.add(normalized)
            result.append(os.path.abspath(path))

    for source in paths:
        source = os.path.abspath(os.fspath(source))
        if os.path.isdir(source):
            for root, dirs, files in os.walk(source, followlinks=False):
                dirs[:] = sorted(d for d in dirs if not os.path.islink(os.path.join(root, d)))
                for name in sorted(files):
                    if os.path.splitext(name)[1].lower() in BOOK_EXTENSIONS:
                        add(os.path.join(root, name))
        elif os.path.isfile(source):
            # Explicitly dropped files follow calibre's normal file import rules.
            add(source)
    return result
