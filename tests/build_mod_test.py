"""Exercise release metadata using a synthetic pack, without original game files."""
import argparse
import importlib.util
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('build_mod', Path(__file__).resolve().parents[1] / 'tools/build_mod.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class ManifestTest(unittest.TestCase):
    def test_general_mod_metadata_and_routine_scripts_survive_packaging(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            pack=root/'pack'
            (pack/'scripts').mkdir(parents=True)
            (pack/'scripts/gameplay.lua').write_text('return {}')
            mod=dict(id='Gameplay',name='Gameplay',summary='Synthetic',authors=['Tester'],type='gameplay',
                     install_dir='packages/Gameplay',dependencies=[{'id':'Assets','version':'1.0.0'}],
                     conflicts=['OtherGameplay'],routines=['scripts/gameplay.lua'],versions=[])
            index=dict(schema=1,game={'xbe_sha256':'0'*64},release_url='https://example.invalid/{id}-{version}.tar.xz',mods=[mod])
            old_index=builder.INDEX
            try:
                builder.INDEX=root/'mods.json'
                builder.INDEX.write_text(json.dumps(index))
                builder.build(argparse.Namespace(mod='Gameplay',version='1.0.0',pack=pack,game=None,
                                                xdelta='xdelta3',output=root/'dist',recomp='0.1.0'))
                with tarfile.open(root/'dist/Gameplay-1.0.0.tar.xz') as archive:
                    manifest=json.load(archive.extractfile('manifest.json'))
                    self.assertEqual(manifest['schema'],2)
                    self.assertEqual(manifest['recomp'],'0.2.0')
                    self.assertEqual(manifest['dependencies'],[{'id':'Assets','version':'1.0.0'}])
                    self.assertEqual(manifest['conflicts'],['OtherGameplay'])
                    self.assertEqual(manifest['routines'],['scripts/gameplay.lua'])
                    self.assertIn('lua',manifest['features'])
                    self.assertEqual(archive.extractfile('scripts/gameplay.lua').read(),b'return {}')
            finally:
                builder.INDEX=old_index

    def test_prerelease_numbers_sort_numerically(self):
        versions=['1.0.0-beta.2','1.0.0-beta.11','1.0.0']
        self.assertEqual(sorted(versions,key=builder.version_key,reverse=True),['1.0.0','1.0.0-beta.11','1.0.0-beta.2'])

    def test_invalid_routine_preserves_existing_release_and_catalog(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            pack=root/'pack'
            pack.mkdir()
            (pack/'README.txt').write_text('Test')
            mod=dict(id='Gameplay',name='Gameplay',summary='Test',authors=['Tester'],type='gameplay',
                     install_dir='packages/Gameplay',routines=['scripts/missing.lua'],versions=[])
            index=dict(schema=1,game={'xbe_sha256':'0'*64},release_url='https://example.invalid/{id}-{version}.tar.xz',mods=[mod])
            out=root/'dist'
            out.mkdir()
            existing=out/'Gameplay-1.0.0.tar.xz'
            existing.write_bytes(b'previous release')
            old_index=builder.INDEX
            try:
                builder.INDEX=root/'mods.json'
                original=json.dumps(index)
                builder.INDEX.write_text(original)
                with self.assertRaises(builder.BuildError):
                    builder.build(argparse.Namespace(mod='Gameplay',version='1.0.0',pack=pack,game=None,
                                                    xdelta='xdelta3',output=out,recomp='0.1.0'))
                self.assertEqual(existing.read_bytes(),b'previous release')
                self.assertEqual(builder.INDEX.read_text(),original)
            finally:
                builder.INDEX=old_index

    def test_local_archive_is_self_describing_and_hashes_all_payload_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pack = root / 'pack'
            pack.mkdir()
            (pack / 'new.txt').write_bytes(b'new mod content')
            mod = dict(id='TestMod', name='Test mod', summary='Synthetic', authors=['Tester'],
                       type='language', language='pt-BR', install_dir='languages/TestMod', versions=[])
            index = dict(schema=1, game={'xbe_sha256': '0' * 64}, release_url='https://example.invalid/{id}-{version}.tar.xz', mods=[mod])
            old_index = builder.INDEX
            try:
                builder.INDEX = root / 'mods.json'
                builder.INDEX.write_text(json.dumps(index), encoding='utf-8')
                builder.build(argparse.Namespace(mod='TestMod', version='1.2.3', pack=pack, game=None,
                                                xdelta='xdelta3', output=root / 'dist', recomp='0.1.0'))
                with tarfile.open(root / 'dist/TestMod-1.2.3.tar.xz') as archive:
                    manifest = json.load(archive.extractfile('manifest.json'))
                    self.assertEqual(manifest['id'], 'TestMod')
                    self.assertEqual(manifest['version'], '1.2.3')
                    self.assertEqual(manifest['install_dir'], 'languages/TestMod')
                    self.assertEqual(manifest['patches'], [])
                    self.assertEqual(manifest['files'], [{'path': 'new.txt', 'size': 15,
                                                         'sha256': builder.sha256(b'new mod content')}])
                    self.assertEqual(archive.extractfile('new.txt').read(), b'new mod content')
            finally:
                builder.INDEX = old_index


if __name__ == '__main__':
    unittest.main()
