# Shaolin Monks Recomp Krypt

Official mods for [Shaolin Monks Recomp](https://github.com/OAleex/ShaolinMonksRecomp).
The project creates and maintains every mod here.

## Install a mod

Install and enable the mod in the launcher's **Mods** tab.
For a downloaded `.tar.xz` package, use **Install from file...**.
The original game files remain unchanged.
The catalog supplies new mods, languages and compatible updates to the launcher.
Supported content includes textures, game resources, direct file replacements and Lua routine scripts.
See [mod authoring](https://github.com/OAleex/ShaolinMonksRecomp/blob/main/docs/MODS.md) for formats and compatibility rules.

## Build a package

Install Python 3.9 or later.
Put xdelta3 in `tools/xdelta3.exe` or on `PATH`.

1. Put the finished files in `mods/<id>/pack/`.
2. Run `build.bat`.
3. Enter the original extracted game folder when requested.
4. Enter the mod version.

The game folder must contain `default.xbe` and the complete game data.
The saved game path is in `game_dir.txt`. Delete it to choose another folder.
Use `build.bat <id>` when the catalog contains multiple mods.

The builder writes a `.tar.xz` to `dist/` and updates `mods.json` with the release information and SHA-256 hash.
Wait for the builder to print `Release tag` before using the package. Files ending in `.building` are incomplete.
The package contains mod files, patches and `mod.json`. Building does not publish it.
For replacements outside `gamedata/`, define the original files in the catalog's `sources` field.

Git ignores the pack files, `dist/`, `game_dir.txt` and `tools/xdelta3.exe`.

## Publish a version

1. Test the generated `.tar.xz` through **Mods > Install from file...**.
2. Review the updated `mods.json`.
3. Commit the catalog change when the release is ready.
4. Create a release with the tag printed by the builder.
5. Attach the `.tar.xz` from `dist/`.

Publish the asset before the updated catalog on `main`.
The launcher reads `mods.json`. Uploading a release alone does not add a mod or update.
Use a higher version for an update and a new catalog entry for a different mod.
