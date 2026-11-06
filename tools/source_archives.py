"""Hash-pinned official source distributions, without running their build/bootstrap code."""
import hashlib
import json
from pathlib import Path
import shutil
import re
import string
import tarfile
import tempfile
import urllib.request

MAX_ARCHIVE = 512 * 1024**2
MAX_EXPANDED = 2 * 1024**3
RECEIPT = '.mochios-source.json'


def release_url(library, version, helpers):
    """Expand only the reviewed release-source template over HTTPS."""
    template = library['source_archive']
    # Upstreams such as ICU use different separators in their release tag and
    # archive filename. Only fixed, numeric version transforms are supported;
    # Python attribute/index access, conversions and format specs are forbidden.
    if not re.fullmatch(r'[0-9]+(?:\.[0-9]+)*', version):
        raise ValueError('Invalid source distribution version')
    values = {'version': version, 'version_dash': version.replace('.', '-'),
              'version_underscore': version.replace('.', '_')}
    fields = []
    for _, field, spec, conversion in string.Formatter().parse(template):
        if field is None:
            continue
        if field not in values or spec or conversion or field in fields:
            raise ValueError('Invalid source archive version placeholder')
        fields.append(field)
    if not fields:
        raise ValueError('Source archive template needs a version placeholder')
    url = template.format(**values)
    if not helpers.https_url(url): raise ValueError('Invalid source distribution URL')
    return url


def resolve(library, selected, baseline, helpers):
    """Freeze distribution bytes alongside the Git tag; an existing release hash never changes silently."""
    url = release_url(library, selected['version'], helpers)
    if selected['version'] == baseline['version']:
        record = baseline.get('archive', {})
        if record.get('url') != url or not helpers.SHA256.fullmatch(record.get('sha256', '')):
            raise ValueError('Release source needs a reviewed archive URL and SHA-256 in sources.lock.json')
    else:
        digest = hashlib.sha256(); total = 0
        with urllib.request.build_opener(helpers.HTTPSRedirects()).open(url, timeout=90) as stream:
            while chunk := stream.read(1024*1024):
                total += len(chunk)
                if total > MAX_ARCHIVE: raise ValueError('Source distribution exceeds size limit')
                digest.update(chunk)
        record = dict(url=url, sha256=digest.hexdigest())
    return dict(selected, archive=record)


def validate(library, selected, baseline, helpers):
    """Keep archive provenance consistent with its frozen version and reviewed upstream location."""
    record = selected.get('archive', {})
    if record.get('url') != release_url(library, selected['version'], helpers) or not helpers.SHA256.fullmatch(record.get('sha256', '')):
        raise ValueError('Invalid frozen source distribution')
    if selected['version'] == baseline['version'] and record != baseline.get('archive'):
        raise ValueError('Pinned source distribution changed')


def checkout(output, library, selected, helpers):
    """Extract authenticated source bytes and reject modified caches, unsafe paths and oversized input."""
    record = selected['archive']; digest = record['sha256']
    if record['url'] != release_url(library, selected['version'], helpers) or not helpers.SHA256.fullmatch(digest):
        raise ValueError('Invalid source archive identity')
    destination = output/'sources'/('dist-'+digest)
    if destination.exists():
        receipt = json.loads((destination/RECEIPT).read_text())
        files = [item for item in helpers.inventory(destination) if item['path'] != RECEIPT]
        if receipt.get('archive') != record or receipt.get('files') != files:
            raise ValueError('Source distribution cache was modified')
        return destination
    cache = output/'downloads'; cache.mkdir(parents=True, exist_ok=True)
    archive = cache/digest
    if not archive.exists():
        with tempfile.NamedTemporaryFile(dir=cache) as temp:
            total = 0
            with urllib.request.build_opener(helpers.HTTPSRedirects()).open(record['url'], timeout=90) as stream:
                while chunk := stream.read(1024*1024):
                    total += len(chunk)
                    if total > MAX_ARCHIVE: raise ValueError('Source distribution exceeds size limit')
                    temp.write(chunk)
            temp.flush()
            if helpers.hash_file(Path(temp.name)) != digest: raise ValueError('Source distribution digest mismatch')
            shutil.copyfile(temp.name, archive)
    if helpers.hash_file(archive) != digest: raise ValueError('Cached source distribution digest mismatch')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent, prefix='distribution-') as temp:
        staged = Path(temp)/'source'; staged.mkdir()
        with tarfile.open(archive) as tar:
            members = helpers.safe_members(tar)
            size = sum(member.size for member in members)
            if len(members) > 100_000 or size > MAX_EXPANDED or size + 256*1024**2 > shutil.disk_usage(staged).free:
                raise ValueError('Source distribution exceeds extraction/free-space budget')
            tar.extractall(staged, members=members, filter='data')
        roots = list(staged.iterdir())
        if len(roots) != 1 or roots[0].is_symlink() or not roots[0].is_dir():
            raise ValueError('Source distribution must have a single enclosing directory')
        source = roots[0]
        if (source/RECEIPT).exists(): raise ValueError('Reserved source distribution metadata')
        files = helpers.inventory(source)
        helpers.write_json(source/RECEIPT, dict(archive=record, files=files))
        source.rename(destination)
    return destination
