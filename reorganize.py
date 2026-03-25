#!/usr/bin/env python3
"""
AnimeSub Repository Reorganizer

Restructures the AnimeSub repository from mixed directory layouts into a
normalized structure: Letter/AnimeName/Season/ZIP+metadata.json

Usage:
    python3 reorganize.py [--dry-run]
"""

import os
import re
import json
import zipfile
import shutil
import sys
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timezone

REPO_ROOT = Path(__file__).parent
SEASON_PATTERN = re.compile(r'^S\d+$', re.IGNORECASE)
SPECIAL_SEASONS = {'movie', 'ova', 'oad', 'sp', 'sps'}
LANG_DIRS = {'sc', 'tc', 'chs', 'cht'}
ASS_EXT = '.ass'
SKIP_EXTS = {'.srt', '.7z', '.ttf', '.otf', '.ttc', '.zip'}
# Language tags found in subtitle filenames
LANG_TAGS = re.compile(
    r'\.(JPSC|JPTC|SC|TC|sc|tc|chs|cht|CHS|CHT|chs_jpn|cht_jpn|'
    r'chs_jp|cht_jp|zh-hans|zh-hant|zh-Hans|zh-Hant)\.',
    re.IGNORECASE
)
# Extract sub group from [xxx] at the start of a filename
SUBGROUP_BRACKET = re.compile(r'^\[([^\]]+)\]')
# Extract anime name after sub group bracket
ANIME_NAME_AFTER_BRACKET = re.compile(
    r'^\[[^\]]+\]\s*(.+?)(?:\s*[-–]\s*\d|\s*\[\d|\s*\[SP|\s*\[OVA|\s*\[Ma\d|\s*\[Web|\s*\[BD|\s*\(\w)',
    re.IGNORECASE
)
# Fallback: anime name from SxxExx pattern
ANIME_NAME_SXXEXX = re.compile(r'^(.+?)\s*[-–]\s*S\d+E\d+')
# Fallback: anime name from " - NN." pattern (like "Title - 01.SubGroup.lang.srt")
ANIME_NAME_DASH_NUM = re.compile(r'^(.+?)\s*[-–]\s*\d+\.')


def classify_dir(dirname):
    """Classify a subdirectory by its name."""
    lower = dirname.lower().strip()
    if SEASON_PATTERN.match(dirname):
        return 'season', dirname
    if lower in SPECIAL_SEASONS:
        return 'season', dirname
    if lower in LANG_DIRS:
        return 'language', dirname
    return 'other', dirname


def extract_subgroup(filename):
    """Extract the sub group name from a filename like [SubGroup] ..."""
    m = SUBGROUP_BRACKET.match(filename)
    if m:
        return m.group(1)
    # Scene release format: Name.Year.S01E01...Codec-SubGroup.lang.ass
    # e.g., BanG.Dream.Ave.Mujica.2025.S01E01.1080p.WEB-Rip.x265.DDP2.0-CoolFansSub.chs.assfonts.ass
    m2 = re.match(r'^.+?[-.]([A-Za-z][A-Za-z0-9]+(?:Sub|Subs|sub|Raws|Fansub|fansub))\.',
                  filename)
    if m2:
        return m2.group(1)
    # Try "Title - S01E01 - SubGroup.lang.ass" pattern
    parts = filename.split(' - ')
    if len(parts) >= 3:
        candidate = parts[-1].split('.')[0]
        if candidate and not re.match(r'^\d+$', candidate):
            return candidate
    return None


def extract_anime_name(filename):
    """Extract the anime romaji/english name from a subtitle filename."""
    # Pattern 1a: [SubGroup][AnimeName][Episode]... (bracket-bracket format)
    # e.g., [FLsnow][Yurikuma_Arashi][01][AVC_AAC][TVRIP].chs.assfonts.ass
    # e.g., [KissSub&FZSD&Xrip][Kono_Subarashii_Sekai_ni_Shukufuku_o!][BDrip][01]...
    m_bb = re.match(r'^\[([^\]]+)\]\[([^\]]+)\]', filename)
    if m_bb:
        candidate = m_bb.group(2)
        # Check it's not an episode number or codec
        if not re.match(r'^\d+$', candidate) and not re.match(
                r'^(?:BDrip|WebRip|Ma\d|HEVC|AVC|AAC|FLAC)', candidate, re.I):
            name = candidate.replace('_', ' ').strip()
            return name

    # Pattern 1b: [SubGroup] AnimeName [Episode]... or [SubGroup] AnimeName - NN ...
    m = ANIME_NAME_AFTER_BRACKET.match(filename)
    if m:
        name = m.group(1).strip()
        name = re.sub(r'\s*\[.*$', '', name).strip()
        name = re.sub(r'\s*[-–]\s*$', '', name).strip()
        # Strip trailing season identifier (e.g., "Goblin Slayer S2" → "Goblin Slayer")
        name = re.sub(r'\s+S\d+\s*$', '', name).strip()
        name = re.sub(r'\s+Season\s+\d+\s*$', '', name, flags=re.I).strip()
        return name

    # Pattern 2: [SubGroup] AnimeName.lang.ass (single file, no episode)
    m2 = re.match(r'^\[([^\]]+)\]\s*(.+?)\.(?:assfonts|' +
                   r'JPSC|JPTC|SC|TC|sc|tc|chs|cht|CHS|CHT|'
                   r'chs_jpn|cht_jpn|zh-hans|zh-hant)', filename)
    if m2:
        name = m2.group(2).strip()
        name = re.sub(r'\s*\[.*$', '', name).strip()
        name = re.sub(r'\s*[-–]\s*$', '', name).strip()
        name = re.sub(r'\s+S\d+\s*$', '', name).strip()
        return name

    # Pattern 3: Scene release: Name.Year.S01E01...Codec-SubGroup.lang.ass
    # e.g., BanG.Dream.Ave.Mujica.2025.S01E01.1080p.WEB-Rip.x265.DDP2.0-CoolFansSub.chs.assfonts.ass
    m_scene = re.match(r'^((?:[A-Za-z0-9!]+\.)+?)(?:\d{4}\.|S\d+E\d+)', filename)
    if m_scene:
        name = m_scene.group(1).rstrip('.').replace('.', ' ').strip()
        return name

    # Pattern 4: "AnimeName - S01E01 - SubGroup.lang.ass"
    m3 = ANIME_NAME_SXXEXX.match(filename)
    if m3:
        return m3.group(1).strip()

    # Pattern 5: Japanese title with episode number
    m4 = re.match(r'^(.+?)\s*第\d+話', filename)
    if m4:
        return m4.group(1).strip()

    # Pattern 6: "AnimeName - NN.SubGroup.lang.srt"
    m5 = ANIME_NAME_DASH_NUM.match(filename)
    if m5:
        return m5.group(1).strip()

    return None


def extract_languages(filenames):
    """Extract unique language tags from a list of filenames."""
    langs = set()
    for fn in filenames:
        for m in LANG_TAGS.finditer(fn):
            langs.add(m.group(1))
    return sorted(langs)


def extract_episode_count(filenames):
    """Estimate episode count from filenames."""
    episodes = set()
    for fn in filenames:
        # [xx] pattern
        for m in re.finditer(r'\[(\d+(?:\.\d+)?)\]', fn):
            episodes.add(m.group(1))
        # SxxExx pattern
        for m in re.finditer(r'S\d+E(\d+)', fn, re.IGNORECASE):
            episodes.add(m.group(1))
        # " - NN." pattern
        m = re.match(r'^.+?\s*[-–]\s*(\d+)\.', fn)
        if m:
            episodes.add(m.group(1))
        # 第NN話 pattern
        m = re.search(r'第(\d+)話', fn)
        if m:
            episodes.add(m.group(1))
    # Also count SP/OVA
    for fn in filenames:
        if re.search(r'\[SP\]|\[OVA\]|\[OAD\]', fn, re.IGNORECASE):
            episodes.add('SP')
        if re.search(r'\bPV\b', fn):
            episodes.add('PV')
    return len(episodes) if episodes else len(filenames)


def collect_ass_files(directory):
    """Collect all .ass files recursively from a directory."""
    ass_files = []
    for root, dirs, files in os.walk(directory):
        for f in files:
            if f.lower().endswith(ASS_EXT):
                ass_files.append(os.path.join(root, f))
    return ass_files


def scan_anime_dir(anime_path, letter, anime_name):
    """
    Scan an anime directory and return a list of packaging groups.

    Each group is a dict with:
    - letter: str
    - anime_name_cn: str
    - sub_entry: str or None (for franchise sub-entries)
    - season: str (S1, S2, Movie, etc.)
    - sub_group: str
    - anime_name_romaji: str
    - ass_files: list of file paths
    - languages: list of language tags
    """
    groups = []
    anime_path = Path(anime_path)

    # First, classify all immediate subdirectories
    subdirs = {}
    has_season_dirs = False
    has_lang_dirs = False
    has_other_dirs = False
    other_dirs = []

    if anime_path.is_dir():
        for item in anime_path.iterdir():
            if item.is_dir() and not item.name.startswith('.'):
                dtype, dname = classify_dir(item.name)
                subdirs[item.name] = dtype
                if dtype == 'season':
                    has_season_dirs = True
                elif dtype == 'language':
                    has_lang_dirs = True
                else:
                    has_other_dirs = True
                    other_dirs.append(item.name)

    # Collect files directly in the anime directory
    direct_files = [
        str(anime_path / f)
        for f in os.listdir(anime_path)
        if os.path.isfile(anime_path / f) and f.lower().endswith(ASS_EXT)
    ]

    # Case 1: Has season directories (S1, S2, Movie, etc.)
    # Process each season
    for dirname, dtype in subdirs.items():
        if dtype == 'season':
            season_path = anime_path / dirname
            ass_files = collect_ass_files(season_path)
            if ass_files:
                _add_groups(groups, ass_files, letter, anime_name, None, dirname)

    # Case 2: Has language directories only (SC/TC) → default to S1
    if has_lang_dirs and not has_season_dirs:
        lang_files = []
        for dirname, dtype in subdirs.items():
            if dtype == 'language':
                lang_files.extend(collect_ass_files(anime_path / dirname))
        if lang_files:
            all_files = lang_files + direct_files
            if all_files:
                _add_groups(groups, all_files, letter, anime_name, None, 'S1')
                direct_files = []  # Already processed

    # Case 3: Has "other" directories (sub groups or franchise entries)
    if has_other_dirs:
        for dirname in other_dirs:
            other_path = anime_path / dirname
            # Check if it's a sub-group directory or a franchise sub-entry
            # Heuristic: if the dirname contains chars like "&" or matches
            # known sub group patterns, treat as sub group
            ass_files = collect_ass_files(other_path)
            if not ass_files:
                continue

            # Check if this is a franchise sub-entry (has its own season/lang structure)
            sub_subdirs = {}
            for item in other_path.iterdir():
                if item.is_dir():
                    dt, dn = classify_dir(item.name)
                    sub_subdirs[item.name] = dt

            has_sub_seasons = any(v == 'season' for v in sub_subdirs.values())

            if has_sub_seasons:
                # Franchise sub-entry with seasons
                for sub_dirname, sub_dtype in sub_subdirs.items():
                    if sub_dtype == 'season':
                        season_files = collect_ass_files(other_path / sub_dirname)
                        if season_files:
                            _add_groups(groups, season_files, letter, anime_name,
                                        dirname, sub_dirname)
                # Also check for direct files or lang dirs
                sub_lang_files = []
                for sub_dirname, sub_dtype in sub_subdirs.items():
                    if sub_dtype == 'language':
                        sub_lang_files.extend(
                            collect_ass_files(other_path / sub_dirname))
                sub_direct = [
                    str(other_path / f) for f in os.listdir(other_path)
                    if os.path.isfile(other_path / f) and f.lower().endswith(ASS_EXT)
                ]
                remaining = sub_lang_files + sub_direct
                if remaining and not any(
                    g['sub_entry'] == dirname and g['season'] == 'S1'
                    for g in groups
                ):
                    _add_groups(groups, remaining, letter, anime_name, dirname, 'S1')
            else:
                # Could be a sub-group dir or a franchise sub-entry without seasons
                # Check if dirname is likely a sub group name
                is_subgroup_dir = _is_likely_subgroup_dir(dirname, ass_files)

                if is_subgroup_dir:
                    # Sub group directory → files go to S1 under the anime
                    # Extract the actual sub group from filenames, not dirname
                    # Check if any file has a bracket sub group
                    first_file_sg = None
                    for f in ass_files[:5]:
                        fn = os.path.basename(f)
                        first_file_sg = extract_subgroup(fn)
                        if first_file_sg:
                            break
                    actual_sg = first_file_sg if first_file_sg else dirname
                    _add_groups(groups, ass_files, letter, anime_name, None, 'S1',
                                force_subgroup=actual_sg)
                else:
                    # Franchise sub-entry without seasons → S1
                    _add_groups(groups, ass_files, letter, anime_name, dirname, 'S1')

    # Case 4: Direct files only → S1
    if direct_files and not groups:
        _add_groups(groups, direct_files, letter, anime_name, None, 'S1')
    elif direct_files:
        # Add direct files to S1 if not already covered
        s1_groups = [g for g in groups if g['season'] == 'S1' and g['sub_entry'] is None]
        if s1_groups:
            for g in s1_groups:
                g['ass_files'].extend(direct_files)
        else:
            _add_groups(groups, direct_files, letter, anime_name, None, 'S1')

    return groups


def _is_likely_subgroup_dir(dirname, ass_files):
    """Check if a directory name is likely a sub group name."""
    # Known Chinese sub group name patterns
    known_cn_subgroups = {
        '北宇治字幕组': 'KitaujiSub',
        '喵萌奶茶屋': 'Nekomoe kissaten',
    }
    if dirname in known_cn_subgroups:
        return True

    # Check if dirname matches or is substring of sub groups in filenames
    for f in ass_files[:5]:
        fn = os.path.basename(f)
        sg = extract_subgroup(fn)
        if sg:
            # Check if dirname is part of the sub group
            if dirname.lower() in sg.lower() or sg.lower() in dirname.lower():
                return True
            # Check without "&" splitting
            parts = [p.strip() for p in sg.split('&')]
            for p in parts:
                if dirname.lower() == p.lower():
                    return True

    # If dirname contains "&" it's likely a sub group
    if '&' in dirname:
        return True

    # If dirname starts with an English name and contains "Subs" or "Sub"
    if re.search(r'\bSubs?\b', dirname, re.IGNORECASE):
        return True

    return False


def _add_groups(groups, ass_files, letter, anime_name_cn, sub_entry, season,
                force_subgroup=None):
    """Group ass files by sub group and add to groups list."""
    by_subgroup = defaultdict(list)

    for f in ass_files:
        fn = os.path.basename(f)
        if force_subgroup:
            sg = force_subgroup
        else:
            sg = extract_subgroup(fn)
        if not sg:
            sg = 'Unknown'
        by_subgroup[sg].append(f)

    for sg, files in by_subgroup.items():
        filenames = [os.path.basename(f) for f in files]
        anime_romaji = None
        for fn in filenames:
            anime_romaji = extract_anime_name(fn)
            if anime_romaji:
                break
        if not anime_romaji:
            anime_romaji = anime_name_cn  # Fallback to Chinese name

        groups.append({
            'letter': letter,
            'anime_name_cn': anime_name_cn,
            'sub_entry': sub_entry,
            'season': season,
            'sub_group': sg,
            'anime_name_romaji': anime_romaji,
            'ass_files': files,
            'languages': extract_languages(filenames),
            'episode_count': extract_episode_count(filenames),
        })


def make_zip_name(group):
    """Generate the ZIP filename for a packaging group."""
    sg = group['sub_group']
    name = group['anime_name_romaji']
    season = group['season']

    # Clean the anime name - remove trailing episode/codec info
    name = re.sub(r'\s*\[.*$', '', name).strip()
    name = re.sub(r'\s*[-–]\s*$', '', name).strip()
    # Remove any trailing dots
    name = name.rstrip('.')
    # Remove trailing season identifiers to avoid "S2 S2" duplication
    name = re.sub(r'\s+S\d+\s*$', '', name).strip()
    name = re.sub(r'\s+Season\s+\d+\s*$', '', name, flags=re.IGNORECASE).strip()
    # Remove trailing " 3rd Season", " 2nd Season" etc.
    name = re.sub(r'\s+\d+(?:st|nd|rd|th)\s+Season\s*$', '', name, flags=re.I).strip()
    # Remove trailing " - Movie" to avoid "Movie Movie"
    name = re.sub(r'\s*[-–]\s*Movie\s*$', '', name, flags=re.I).strip()
    # Remove trailing episode number like "01" (from "[SubGroup] Title 01 [...]" format)
    name = re.sub(r'\s+\d{1,2}\s*$', '', name).strip()

    return f"[{sg}] {name} {season}.zip"


def build_target_path(group):
    """Build the target directory path for a group."""
    parts = [group['letter'], group['anime_name_cn']]
    if group['sub_entry']:
        parts.append(group['sub_entry'])
    parts.append(group['season'])
    return os.path.join(*parts)


def create_zip(group, target_dir, dry_run=False):
    """Create a ZIP file for a packaging group."""
    zip_name = make_zip_name(group)
    zip_path = os.path.join(target_dir, zip_name)

    if dry_run:
        print(f"  [DRY-RUN] Would create: {zip_path}")
        print(f"            Files: {len(group['ass_files'])}")
        return zip_name, 0

    os.makedirs(target_dir, exist_ok=True)

    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(group['ass_files']):
            arcname = os.path.basename(f)
            zf.write(f, arcname)

    size = os.path.getsize(zip_path)
    return zip_name, size


def generate_season_metadata(groups, target_dir, dry_run=False):
    """Generate metadata.json for a season directory."""
    if not groups:
        return

    first = groups[0]
    all_langs = set()
    all_subgroups = set()
    all_formats = set()
    total_episodes = 0
    archives = []

    for g in groups:
        all_langs.update(g['languages'])
        # Parse sub groups from the group's sub_group field
        for sg in g['sub_group'].split('&'):
            all_subgroups.add(sg.strip())
        all_formats.add('ass')
        total_episodes = max(total_episodes, g['episode_count'])

        zip_name = make_zip_name(g)
        zip_path = os.path.join(target_dir, zip_name)
        size = os.path.getsize(zip_path) if os.path.exists(zip_path) else 0

        archives.append({
            'filename': zip_name,
            'size_bytes': size,
            'file_count': len(g['ass_files']),
            'languages': g['languages'],
        })

    metadata = {
        'name_cn': first['anime_name_cn'],
        'letter': first['letter'],
        'sub_groups': sorted(all_subgroups),
        'subtitle_format': sorted(all_formats),
        'languages': sorted(all_langs),
        'season': first['season'],
        'episode_count': total_episodes,
        'has_fonts': False,
        'archives': archives,
    }

    meta_path = os.path.join(target_dir, 'metadata.json')
    if dry_run:
        print(f"  [DRY-RUN] Would create: {meta_path}")
    else:
        with open(meta_path, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, ensure_ascii=False, indent=2)

    return metadata


def generate_anime_metadata(anime_cn, letter, season_metas, target_dir,
                            sub_entries=None, dry_run=False):
    """Generate the anime-level metadata.json."""
    all_seasons = set()
    all_subgroups = set()
    all_formats = set()
    all_langs = set()
    total_archives = 0

    for sm in season_metas:
        all_seasons.add(sm['season'])
        all_subgroups.update(sm['sub_groups'])
        all_formats.update(sm['subtitle_format'])
        all_langs.update(sm['languages'])
        total_archives += len(sm['archives'])

    metadata = {
        'name_cn': anime_cn,
        'letter': letter,
        'seasons': sorted(all_seasons),
        'total_archives': total_archives,
        'sub_groups': sorted(all_subgroups),
        'subtitle_format': sorted(all_formats),
        'languages': sorted(all_langs),
    }

    if sub_entries:
        metadata['sub_entries'] = sub_entries

    meta_path = os.path.join(target_dir, 'metadata.json')
    if dry_run:
        print(f"  [DRY-RUN] Would create: {meta_path}")
    else:
        with open(meta_path, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, ensure_ascii=False, indent=2)

    return metadata


def generate_index(all_anime_metas, dry_run=False):
    """Generate the root index.json."""
    entries = []
    total_archives = 0

    for anime_meta, anime_path in all_anime_metas:
        entry = {
            'name_cn': anime_meta['name_cn'],
            'letter': anime_meta['letter'],
            'path': anime_path,
            'seasons': anime_meta['seasons'],
            'sub_groups': anime_meta['sub_groups'],
            'languages': anime_meta['languages'],
        }
        if 'sub_entries' in anime_meta:
            entry['sub_entries'] = anime_meta['sub_entries']
        entries.append(entry)
        total_archives += anime_meta['total_archives']

    index = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'total_anime': len(entries),
        'total_archives': total_archives,
        'entries': sorted(entries, key=lambda x: (x['letter'], x['name_cn'])),
    }

    index_path = os.path.join(REPO_ROOT, 'index.json')
    if dry_run:
        print(f"  [DRY-RUN] Would create: {index_path}")
    else:
        with open(index_path, 'w', encoding='utf-8') as f:
            json.dump(index, f, ensure_ascii=False, indent=2)

    return index


def cleanup_files(dry_run=False):
    """Remove all original files, keeping only .zip, .json, and .git."""
    removed_count = 0
    for root, dirs, files in os.walk(REPO_ROOT):
        # Skip .git directory
        if '.git' in root.split(os.sep):
            continue
        dirs[:] = [d for d in dirs if d != '.git']

        for f in files:
            fp = os.path.join(root, f)
            ext = os.path.splitext(f)[1].lower()
            if ext in {'.zip', '.json', '.py'}:
                continue
            if dry_run:
                print(f"  [DRY-RUN] Would delete: {fp}")
            else:
                os.remove(fp)
            removed_count += 1

    # Remove empty directories (bottom-up)
    for root, dirs, files in os.walk(REPO_ROOT, topdown=False):
        if '.git' in root.split(os.sep):
            continue
        for d in dirs:
            if d == '.git':
                continue
            dp = os.path.join(root, d)
            try:
                if not os.listdir(dp):
                    if dry_run:
                        print(f"  [DRY-RUN] Would remove empty dir: {dp}")
                    else:
                        os.rmdir(dp)
            except OSError:
                pass

    return removed_count


def verify(dry_run=False):
    """Verify the final structure."""
    issues = []
    zip_count = 0
    meta_count = 0

    for root, dirs, files in os.walk(REPO_ROOT):
        if '.git' in root.split(os.sep):
            continue
        for f in files:
            fp = os.path.join(root, f)
            if f.endswith('.zip'):
                zip_count += 1
                try:
                    with zipfile.ZipFile(fp, 'r') as zf:
                        bad = zf.testzip()
                        if bad:
                            issues.append(f"Corrupt ZIP entry in {fp}: {bad}")
                except Exception as e:
                    issues.append(f"Cannot open ZIP {fp}: {e}")
            elif f == 'metadata.json' or f == 'index.json':
                meta_count += 1
                try:
                    with open(fp, 'r', encoding='utf-8') as fh:
                        json.load(fh)
                except Exception as e:
                    issues.append(f"Invalid JSON {fp}: {e}")

    # Check index.json exists
    index_path = os.path.join(REPO_ROOT, 'index.json')
    if not os.path.exists(index_path):
        issues.append("Missing index.json at root")

    return zip_count, meta_count, issues


def main():
    dry_run = '--dry-run' in sys.argv

    if dry_run:
        print("=" * 60)
        print("DRY RUN MODE - No changes will be made")
        print("=" * 60)

    print("\n[1/5] Analyzing repository structure...")

    # Discover all anime directories
    all_groups = []
    anime_dirs = []

    for letter_dir in sorted(REPO_ROOT.iterdir()):
        if not letter_dir.is_dir() or letter_dir.name.startswith('.'):
            continue
        if len(letter_dir.name) != 1:
            continue

        letter = letter_dir.name
        for anime_dir in sorted(letter_dir.iterdir()):
            if not anime_dir.is_dir() or anime_dir.name.startswith('.'):
                continue
            anime_name = anime_dir.name
            anime_dirs.append((letter, anime_name, str(anime_dir)))

            groups = scan_anime_dir(str(anime_dir), letter, anime_name)
            all_groups.extend(groups)

    print(f"  Found {len(anime_dirs)} anime directories")
    print(f"  Generated {len(all_groups)} packaging groups")

    # Print summary
    total_files = sum(len(g['ass_files']) for g in all_groups)
    print(f"  Total .ass files to package: {total_files}")

    if dry_run:
        print("\n  Packaging groups:")
        for g in all_groups:
            target = build_target_path(g)
            zip_name = make_zip_name(g)
            print(f"    {target}/{zip_name} ({len(g['ass_files'])} files)")

    print("\n[2/5] Creating ZIP packages...")
    for g in all_groups:
        target_dir = os.path.join(REPO_ROOT, build_target_path(g))
        zip_name, size = create_zip(g, target_dir, dry_run)
        if not dry_run:
            print(f"  Created: {build_target_path(g)}/{zip_name} "
                  f"({len(g['ass_files'])} files, {size:,} bytes)")

    print("\n[3/5] Generating metadata...")

    # Group by anime (and sub-entry) for metadata generation
    # Key: (letter, anime_name_cn, sub_entry or None)
    anime_groups = defaultdict(lambda: defaultdict(list))
    for g in all_groups:
        key = (g['letter'], g['anime_name_cn'], g['sub_entry'])
        anime_groups[key][g['season']].append(g)

    all_anime_metas = []

    # For each unique anime (letter, name), collect all metadata
    # Some anime have sub_entries (franchise sub-works)
    anime_by_top = defaultdict(list)
    for (letter, anime_cn, sub_entry), seasons in anime_groups.items():
        anime_by_top[(letter, anime_cn)].append((sub_entry, seasons))

    for (letter, anime_cn), entries in sorted(anime_by_top.items()):
        anime_base_dir = os.path.join(REPO_ROOT, letter, anime_cn)
        season_metas_all = []
        sub_entry_names = []

        for sub_entry, seasons in entries:
            if sub_entry:
                sub_entry_names.append(sub_entry)
                sub_base = os.path.join(anime_base_dir, sub_entry)
            else:
                sub_base = anime_base_dir

            for season, season_groups in sorted(seasons.items()):
                season_dir = os.path.join(sub_base, season)
                sm = generate_season_metadata(season_groups, season_dir, dry_run)
                if sm:
                    season_metas_all.append(sm)

            # If sub_entry, generate sub-entry level metadata
            if sub_entry:
                sub_season_metas = []
                for season, season_groups in sorted(seasons.items()):
                    season_dir = os.path.join(sub_base, season)
                    # Re-read the metadata we just wrote
                    sm_for_sub = {
                        'season': season,
                        'sub_groups': sorted(set(
                            sg.strip()
                            for g in season_groups
                            for sg in g['sub_group'].split('&')
                        )),
                        'subtitle_format': ['ass'],
                        'languages': sorted(set(
                            l for g in season_groups for l in g['languages']
                        )),
                        'archives': [{
                            'filename': make_zip_name(g),
                            'file_count': len(g['ass_files']),
                            'languages': g['languages'],
                        } for g in season_groups],
                    }
                    sub_season_metas.append(sm_for_sub)

                generate_anime_metadata(
                    sub_entry, letter,
                    [sm for sm in season_metas_all if True],  # use all
                    sub_base, dry_run=dry_run
                )

        # Generate top-level anime metadata
        anime_meta = generate_anime_metadata(
            anime_cn, letter, season_metas_all, anime_base_dir,
            sub_entries=sub_entry_names if sub_entry_names else None,
            dry_run=dry_run
        )
        rel_path = os.path.join(letter, anime_cn)
        all_anime_metas.append((anime_meta, rel_path))

    print(f"  Generated metadata for {len(all_anime_metas)} anime")

    print("\n[3.5/5] Generating index.json...")
    index = generate_index(all_anime_metas, dry_run)
    print(f"  Index contains {index['total_anime']} anime, "
          f"{index['total_archives']} archives")

    print("\n[4/5] Cleaning up original files...")
    removed = cleanup_files(dry_run)
    print(f"  Removed {removed} files")

    print("\n[5/5] Verifying final structure...")
    if not dry_run:
        zip_count, meta_count, issues = verify()
        print(f"  ZIP files: {zip_count}")
        print(f"  Metadata files: {meta_count}")
        if issues:
            print(f"  ⚠️  Issues found: {len(issues)}")
            for issue in issues:
                print(f"    - {issue}")
        else:
            print("  ✅ All checks passed!")
    else:
        print("  [DRY-RUN] Skipping verification")

    print("\n" + "=" * 60)
    if dry_run:
        print("DRY RUN COMPLETE. Run without --dry-run to apply changes.")
    else:
        print("REORGANIZATION COMPLETE!")
    print("=" * 60)


if __name__ == '__main__':
    main()
