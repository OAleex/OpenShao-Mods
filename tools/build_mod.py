import argparse
import datetime
import hashlib
import io
import json
import lzma
import re
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / 'mods.json'
RECORD = re.compile(r'^gamedata/record(\d+)\.[a-z0-9]+$', re.IGNORECASE)
VERSION = re.compile(r'^\d+\.\d+\.\d+(?:-[A-Za-z0-9.]+)?$')
# a fixed timestamp keeps the archive identical when nothing changed
ARCHIVE_TIME = 1577836800


class BuildError(Exception):
    pass


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as file:
        for block in iter(lambda: file.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def version_key(version):
    if not VERSION.fullmatch(version):
        raise BuildError('Version must use major.minor.patch with an optional prerelease')
    core, _, pre = version.partition('-')
    if any(len(part)>1 and part.startswith('0') for part in core.split('.')):
        raise BuildError('Version numbers must not contain leading zeroes')
    parts=pre.split('.') if pre else []
    if any(not part or part.isdigit() and len(part)>1 and part.startswith('0') for part in parts):
        raise BuildError('Invalid prerelease version')
    return tuple(int(part) for part in core.split('.')), pre == '', tuple((0,int(part)) if part.isdigit() else (1,part) for part in parts)


def safe_path(value):
    if not isinstance(value,str) or not value or '\\' in value or ':' in value or value.startswith('/'):
        return False
    return all(part not in ('','.','..') and not part.endswith((' ','.')) for part in value.split('/'))


def metadata(mod, files, requested):
    mod_id=mod.get('id','')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',mod_id):
        raise BuildError('Invalid mod ID')
    kind=mod.get('type')
    if kind not in ('language','texture','data','gameplay','mixed'):
        raise BuildError('Unsupported mod type')
    if mod.get('install_dir') not in ('packages/'+mod_id,'languages/'+mod_id if kind=='language' else ''):
        raise BuildError('Use packages/<id> or languages/<id> for the installation directory')
    if kind=='language' and not re.fullmatch(r'[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*',mod.get('language','')):
        raise BuildError('A language mod needs a valid language code')
    if kind!='language' and mod.get('language'):
        raise BuildError('Only language mods can declare a language code')
    paths=set(files)
    routines=mod.get('routines',[])
    if len(set(routines))!=len(routines):
        raise BuildError('Duplicate routine entry script')
    for script in routines:
        if not safe_path(script) or not script.startswith('scripts/') or not script.endswith('.lua') or script not in paths:
            raise BuildError('Each routine must name an existing scripts/*.lua file in the pack')
    for dependency in mod.get('dependencies',[]):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',dependency.get('id','')) or dependency['id']==mod_id:
            raise BuildError('Invalid mod dependency')
        version_key(dependency['version'])
    for conflict in mod.get('conflicts',[]):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',conflict) or conflict==mod_id:
            raise BuildError('Invalid mod conflict')
    features=set(mod.get('features',[]))
    if routines:features.add('lua')
    for path in paths:
        for prefix,feature in [('files/','files'),('gamedata/','gamedata'),('audio/voices/','voices'),('fonts/','fonts'),('audio/movies/','movies')]:
            if path.startswith(prefix):features.add(feature)
    extended=kind!='language' or mod.get('language')!='pt-BR' or mod.get('install_dir')!='languages/'+mod_id or routines or mod.get('dependencies') or mod.get('conflicts') or 'files' in features
    minimum='0.2.0' if extended else '0.1.0'
    recomp=max((requested,minimum),key=version_key)
    result={key:mod[key] for key in ('id','name','summary','authors','type','language','language_name','install_dir','dependencies','conflicts') if key in mod}
    result.update(features=sorted(features),routines=routines)
    return result,2 if extended else 1,recomp


def game_path(game, name):
    """A game file by name, ignoring case, since extracted discs differ in case between tools."""
    path = game
    for part in name.replace('\\', '/').split('/'):
        exact = path / part
        if exact.exists():
            path = exact
            continue
        match = next((p for p in path.iterdir() if p.name.lower() == part.lower()), None) if path.is_dir() else None
        if match is None:
            raise BuildError(f'{name} is not in the game folder')
        path = match
    return path


class Wad:
    """Reads stored records from a PWF archive (GAMEDATA.WAD and its GAMEDATA.WAE, .WAF... volumes)."""

    def __init__(self, game, name):
        self.game, self.name = game, name
        with open(game_path(game, f'{name}.WAD'), 'rb') as file:
            header = file.read(32)
            if len(header) != 32 or header[:4] != b'PWF ':
                raise BuildError(f'{name}.WAD is not a PWF archive')
            count, self.stride = struct.unpack_from('<II', header, 12)
            if not 0 < count <= 100000 or not self.stride:
                raise BuildError(f'{name}.WAD has an invalid directory')
            self.table = file.read(count * 8)
        if len(self.table) != count * 8:
            raise BuildError(f'{name}.WAD has a truncated directory')

    def record(self, number):
        if not 0 <= number < len(self.table) // 8:
            raise BuildError(f'{self.name} has no record {number}')
        first, second = struct.unpack_from('<II', self.table, number * 8)
        if second & 0x80000000:
            offset, size = (first & 0x1FFFFF) * 0x800, ((first >> 22) | (second << 10)) & 0x3FFFFF
        else:
            offset, size = first, second
        volume, within = divmod(offset, self.stride)
        if volume > 22:
            raise BuildError(f'{self.name} record {number} points to an invalid volume')
        with open(game_path(self.game, f'{self.name}.WA{chr(ord("D") + volume)}'), 'rb') as file:
            file.seek(within)
            data = file.read(size)
        if len(data) != size:
            raise BuildError(f'{self.name} record {number} runs past the end of its volume')
        return data


class Game:
    def __init__(self, folder, xbe_sha256):
        self.folder = folder
        self.wads = {}
        if sha256_file(game_path(folder, 'default.xbe')) != xbe_sha256:
            raise BuildError(f'{folder} is not the supported game: default.xbe does not match mods.json')

    def original(self, source):
        if 'wad' in source:
            if source['wad'] not in self.wads:
                self.wads[source['wad']] = Wad(self.folder, source['wad'])
            return self.wads[source['wad']].record(int(source['record']))
        data = game_path(self.folder, source['file']).read_bytes()
        if 'offset' in source:
            start, size = int(source['offset']), int(source['size'])
            if start + size > len(data):
                raise BuildError(f'the range given for {source["file"]} runs past the end of the file')
            data = data[start:start + size]
        return data


def source_of(mod, rel):
    if rel in mod.get('sources', {}):
        return mod['sources'][rel]
    match = RECORD.match(rel)
    if match:
        return {'wad': 'GAMEDATA', 'record': int(match.group(1))}
    return None


def add(archive, name, path):
    info = tarfile.TarInfo(name)
    info.size, info.mode, info.mtime = path.stat().st_size, 0o644, ARCHIVE_TIME
    with open(path, 'rb') as file:
        archive.addfile(info, file)


def build(args):
    index = json.loads(INDEX.read_text(encoding='utf-8'))
    mods = index['mods']
    if args.mod is None:
        if len(mods) != 1:
            raise BuildError('name the mod to build: ' + ', '.join(m['id'] for m in mods))
        args.mod = mods[0]['id']
    mod = next((m for m in mods if m['id'] == args.mod), None)
    if not mod:
        raise BuildError(f'{args.mod} is not in mods.json')
    version_key(args.version)
    pack = (args.pack or ROOT / 'mods' / mod['id'] / 'pack').resolve()
    files = sorted(p for p in pack.rglob('*') if p.is_file()) if pack.is_dir() else []
    if not files:
        raise BuildError(f'put the finished mod files in {pack}')
    if any(path.is_symlink() for path in pack.rglob('*')):
        raise BuildError('Pack directories and files must not be symbolic links')
    relative=[path.relative_to(pack).as_posix() for path in files]
    if any(not safe_path(name) for name in relative) or len({name.lower() for name in relative})!=len(relative):
        raise BuildError('Pack filenames must be safe and unique without case distinctions')
    manifest_base,schema,recomp=metadata(mod,relative,args.recomp)
    if mod.get('cover') and not (ROOT / mod['cover']).is_file():
        print(f'warning: cover image {mod["cover"]} is missing', file=sys.stderr)

    plain, patched = [], []
    for path in files:
        rel = path.relative_to(pack).as_posix()
        if rel == 'manifest.json' or rel.endswith('.xdelta') or path.is_symlink():
            raise BuildError(f'{rel} is reserved or is a symbolic link')
        source = source_of(mod, rel)
        (patched if source else plain).append((rel, path, source))
    game = None
    if patched:
        if not args.game:
            raise BuildError(f'{len(patched)} files replace game files; give the game folder with --game')
        if not shutil.which(args.xdelta):
            raise BuildError(f'{args.xdelta} was not found; install xdelta3 or give its path with --xdelta')
        game = Game(args.game.resolve(), index['game']['xbe_sha256'])

    args.output.mkdir(parents=True, exist_ok=True)
    archive_path = args.output / f'{mod["id"]}-{args.version}.tar.xz'
    temporary_path=archive_path.with_name(archive_path.name+'.building')
    entries = []
    print('building the archive, this takes a few minutes', flush=True)
    # one solid xz stream at the highest preset: similar files, such as a movie's stereo and surround audio,
    # compress against each other, which a ZIP's per-file compression cannot do
    with tempfile.TemporaryDirectory() as work, \
            tarfile.open(temporary_path, 'w:xz', format=tarfile.USTAR_FORMAT, preset=9 | lzma.PRESET_EXTREME) as archive:
        for rel, path, _ in plain:
            add(archive, rel, path)
        original, delta = Path(work) / 'original', Path(work) / 'patch.xdelta'
        for number, (rel, path, source) in enumerate(patched, 1):
            print(f'\rpatch {number}/{len(patched)}', end='', flush=True)
            data = game.original(source)
            if sha256(data) == sha256_file(path):
                raise BuildError(f'{rel} is the same as the original; remove it from the pack')
            original.write_bytes(data)
            # -S none: no secondary compression, so the launcher only has to read plain VCDIFF
            result = subprocess.run([args.xdelta, '-e', '-f', '-9', '-S', 'none', '-s', str(original), str(path),
                                     str(delta)], capture_output=True, text=True)
            if result.returncode:
                raise BuildError(f'xdelta3 failed on {rel}: {result.stderr.strip()}')
            add(archive, rel + '.xdelta', delta)
            entries.append({'path': rel, 'source': source, 'source_sha256': sha256(data),
                            'target_sha256': sha256_file(path)})
        if patched:
            print()
        manifest = dict(manifest_base)
        manifest.update(schema=schema, version=args.version, recomp=recomp, patches=entries,
                        game=index['game'],
                        files=[{'path': rel, 'size': path.stat().st_size, 'sha256': sha256_file(path)}
                               for rel, path, _ in plain])
        encoded = (json.dumps(manifest, indent=2, ensure_ascii=False) + '\n').encode('utf-8')
        info = tarfile.TarInfo('manifest.json')
        info.size, info.mode, info.mtime = len(encoded), 0o644, ARCHIVE_TIME
        archive.addfile(info, io.BytesIO(encoded))

    temporary_path.replace(archive_path)

    release = {
        'version': args.version,
        'date': datetime.date.today().isoformat(),
        'recomp': recomp,
        'url': index['release_url'].format(id=mod['id'], version=args.version),
        'size': archive_path.stat().st_size,
        'sha256': sha256_file(archive_path),
        'files': len(files),
        'patches': entries,
    }
    versions = [v for v in mod.get('versions', []) if v['version'] != args.version] + [release]
    mod['versions'] = sorted(versions, key=lambda v: version_key(v['version']), reverse=True)
    mod['features']=manifest_base['features']
    temporary_index=INDEX.with_name(INDEX.name+'.building')
    temporary_index.write_text(json.dumps(index, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    temporary_index.replace(INDEX)

    print(f'{archive_path} ({release["size"] / 1024 ** 2:.1f} MiB)')
    print(f'{len(plain)} files as they are, {len(patched)} patches')
    print(f'Release tag: {mod["id"]}-v{args.version}')


def main():
    parser = argparse.ArgumentParser(description='Build a Krypt mod release.')
    parser.add_argument('mod', nargs='?', help='mod id in mods.json; optional when there is only one mod')
    parser.add_argument('--version', required=True)
    parser.add_argument('--game', type=Path, help='extracted game folder, with default.xbe and GAMEDATA.WAD')
    parser.add_argument('--pack', type=Path, help='finished mod files (default: mods/<id>/pack)')
    parser.add_argument('--recomp', default='0.1.0', help='oldest Shaolin Monks Recomp version the mod supports')
    parser.add_argument('--xdelta', default='xdelta3', help='xdelta3 executable')
    parser.add_argument('--output', type=Path, default=ROOT / 'dist')
    args = parser.parse_args()
    try:
        build(args)
    except (BuildError, OSError) as error:
        parser.exit(1, f'error: {error}\n')


if __name__ == '__main__':
    main()
