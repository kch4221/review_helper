#!/usr/bin/env python3
import sqlite3
import os
import json
import sys
import re
import random
import subprocess
import tempfile
from datetime import datetime, timedelta

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(SKILL_DIR, 'data.sqlite3')
LOCAL_REF_DIR = os.path.join(SKILL_DIR, 'local_reference')


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# ---------------------------------------------------------------------------
# Database initialization & migration
# ---------------------------------------------------------------------------

def initialize_database():
    os.makedirs(LOCAL_REF_DIR, exist_ok=True)
    conn = get_connection()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            type TEXT NOT NULL CHECK(type IN ('book', 'course', 'url', 'file', 'youtube')),
            local_path TEXT,
            url TEXT,
            tags TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS knowledge_points (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id INTEGER,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            chapter TEXT DEFAULT '',
            tags TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            next_review_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            interval_days REAL DEFAULT 0,
            ease_factor REAL DEFAULT 2.5,
            review_count INTEGER DEFAULT 0,
            last_review_at TIMESTAMP,
            FOREIGN KEY (source_id) REFERENCES sources(id)
        );

        CREATE TABLE IF NOT EXISTS review_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            knowledge_point_id INTEGER NOT NULL,
            review_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            remember_level INTEGER NOT NULL CHECK(remember_level BETWEEN 0 AND 5),
            FOREIGN KEY (knowledge_point_id) REFERENCES knowledge_points(id)
        );
    """)

    # Migrations for existing databases
    _migrate_add_column(conn, 'sources', 'tags', "TEXT DEFAULT ''")

    conn.commit()
    conn.close()
    print(json.dumps({"status": "ok", "message": "Database initialized successfully."}))


def _migrate_add_column(conn, table, column, col_def):
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}")
    except sqlite3.OperationalError:
        pass


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------

def add_source(title, source_type, local_path=None, url=None, tags=None):
    conn = get_connection()
    cursor = conn.execute(
        "INSERT INTO sources (title, type, local_path, url, tags) VALUES (?, ?, ?, ?, ?)",
        (title, source_type, local_path, url, tags or '')
    )
    source_id = cursor.lastrowid
    conn.commit()
    conn.close()
    print(json.dumps({"source_id": source_id}))


def search_sources(query, limit=10):
    """Search sources by title, tags, or url to find related content & reuse tags."""
    conn = get_connection()
    pattern = f"%{query}%"
    rows = conn.execute("""
        SELECT s.*, COUNT(kp.id) as point_count
        FROM sources s
        LEFT JOIN knowledge_points kp ON s.id = kp.source_id
        WHERE s.title LIKE ? OR s.tags LIKE ? OR s.url LIKE ?
        GROUP BY s.id
        ORDER BY s.created_at DESC
        LIMIT ?
    """, (pattern, pattern, pattern, limit)).fetchall()
    conn.close()

    output = [dict(row) for row in rows]
    all_tags = set()
    for row in output:
        if row.get('tags'):
            all_tags.update(t.strip() for t in row['tags'].split(',') if t.strip())

    print(json.dumps({
        "sources": output,
        "count": len(output),
        "existing_tags": sorted(all_tags),
    }, ensure_ascii=False, indent=2))


def list_sources():
    conn = get_connection()
    rows = conn.execute("""
        SELECT s.*, COUNT(kp.id) as point_count
        FROM sources s
        LEFT JOIN knowledge_points kp ON s.id = kp.source_id
        GROUP BY s.id
        ORDER BY s.created_at DESC
    """).fetchall()
    conn.close()

    all_tags = set()
    output = []
    for row in rows:
        d = dict(row)
        output.append(d)
        if d.get('tags'):
            all_tags.update(t.strip() for t in d['tags'].split(',') if t.strip())

    print(json.dumps({
        "sources": output,
        "all_tags": sorted(all_tags),
    }, ensure_ascii=False, indent=2))


def get_source_content(source_id):
    """Return source metadata and its local file content (for detailed explanation)."""
    conn = get_connection()
    row = conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
    conn.close()

    if not row:
        print(json.dumps({"error": f"Source {source_id} not found"}))
        return

    source = dict(row)
    local_path = source.get('local_path')

    if not local_path:
        print(json.dumps({"source": source, "content": None,
                           "message": "No local file path recorded."},
                          ensure_ascii=False, indent=2))
        return

    full_path = os.path.join(SKILL_DIR, local_path)
    if not os.path.exists(full_path):
        print(json.dumps({"source": source, "content": None,
                           "file_path": full_path,
                           "message": "Local file not found on disk."},
                          ensure_ascii=False, indent=2))
        return

    file_size = os.path.getsize(full_path)
    with open(full_path, 'r', encoding='utf-8', errors='replace') as f:
        content = f.read()

    print(json.dumps({
        "source": source,
        "file_path": full_path,
        "file_size_bytes": file_size,
        "content": content,
    }, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Knowledge points
# ---------------------------------------------------------------------------

def add_knowledge_points(source_id, points):
    conn = get_connection()
    now = datetime.now().isoformat()
    ids = []
    for p in points:
        cursor = conn.execute(
            """INSERT INTO knowledge_points
               (source_id, title, content, chapter, tags, next_review_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (source_id, p['title'], p['content'],
             p.get('chapter', ''), p.get('tags', ''), now)
        )
        ids.append(cursor.lastrowid)
    conn.commit()
    conn.close()
    print(json.dumps({"added": len(ids), "ids": ids}))


def search_knowledge(query, limit=10):
    conn = get_connection()
    pattern = f"%{query}%"
    rows = conn.execute("""
        SELECT kp.*, s.title as source_title, s.local_path as source_local_path
        FROM knowledge_points kp
        LEFT JOIN sources s ON kp.source_id = s.id
        WHERE kp.title LIKE ? OR kp.content LIKE ?
              OR kp.tags LIKE ? OR kp.chapter LIKE ?
        LIMIT ?
    """, (pattern, pattern, pattern, pattern, limit)).fetchall()
    conn.close()

    output = []
    for row in rows:
        r = dict(row)
        output.append({
            "id": r['id'],
            "title": r['title'],
            "content": r['content'],
            "chapter": r['chapter'],
            "tags": r['tags'],
            "source_id": r['source_id'],
            "source_title": r.get('source_title', ''),
            "source_local_path": r.get('source_local_path', ''),
            "review_count": r['review_count'],
            "last_review_at": r['last_review_at'],
        })

    print(json.dumps({"results": output, "count": len(output)}, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
# Review scheduling (SM-2 algorithm)
# ---------------------------------------------------------------------------

def get_review_candidates(limit=5, topic=None):
    conn = get_connection()
    now = datetime.now()

    query = """
        SELECT kp.*, s.title as source_title, s.local_path as source_local_path
        FROM knowledge_points kp
        LEFT JOIN sources s ON kp.source_id = s.id
        WHERE 1=1
    """
    params = []

    if topic:
        query += (" AND (kp.tags LIKE ? OR kp.title LIKE ? "
                   "OR kp.content LIKE ? OR kp.chapter LIKE ? "
                   "OR s.tags LIKE ?)")
        pattern = f"%{topic}%"
        params.extend([pattern] * 5)

    rows = conn.execute(query, params).fetchall()
    conn.close()

    if not rows:
        print(json.dumps({"candidates": [], "message": "No knowledge points found."}))
        return

    candidates = []
    for row in rows:
        d = dict(row)
        next_review = (datetime.fromisoformat(d['next_review_at'])
                       if d['next_review_at'] else now)
        interval = max(d['interval_days'], 0.5)

        overdue_days = (now - next_review).total_seconds() / 86400
        urgency = overdue_days / interval if interval > 0 else overdue_days

        if d['review_count'] == 0:
            urgency = max(urgency, 10.0)

        d['urgency_score'] = round(urgency, 3)
        candidates.append(d)

    due_items = [c for c in candidates if c['urgency_score'] >= 0]
    if not due_items:
        print(json.dumps({"candidates": [], "message": "No knowledge points due for review."}))
        return

    if len(due_items) <= limit:
        selected = due_items
    else:
        weights = [max(c['urgency_score'], 0.1) for c in due_items]
        pool = random.choices(due_items, weights=weights, k=limit * 3)
        seen = set()
        selected = []
        for x in pool:
            if x['id'] not in seen:
                seen.add(x['id'])
                selected.append(x)
            if len(selected) >= limit:
                break

    output = []
    for c in selected:
        output.append({
            "id": c['id'],
            "title": c['title'],
            "content": c['content'],
            "chapter": c['chapter'],
            "tags": c['tags'],
            "source_id": c['source_id'],
            "source_title": c.get('source_title', ''),
            "source_local_path": c.get('source_local_path', ''),
            "review_count": c['review_count'],
            "urgency_score": c['urgency_score'],
            "last_review_at": c['last_review_at'],
        })

    print(json.dumps({"candidates": output}, ensure_ascii=False, indent=2))


def record_review(point_id, remember_level):
    conn = get_connection()
    now = datetime.now()

    row = conn.execute(
        "SELECT * FROM knowledge_points WHERE id = ?", (point_id,)
    ).fetchone()

    if not row:
        print(json.dumps({"error": f"Knowledge point {point_id} not found"}))
        conn.close()
        return

    row = dict(row)
    interval = row['interval_days']
    ease_factor = row['ease_factor']
    review_count = row['review_count']

    if remember_level >= 3:
        if review_count == 0:
            interval = 1
        elif review_count == 1:
            interval = 6
        else:
            interval = interval * ease_factor
        review_count += 1
    else:
        interval = 1
        review_count = 0

    ease_factor = max(
        1.3,
        ease_factor + (0.1 - (5 - remember_level) * (0.08 + (5 - remember_level) * 0.02))
    )

    next_review = now + timedelta(days=interval)

    conn.execute("""
        UPDATE knowledge_points
        SET interval_days = ?, ease_factor = ?, review_count = ?,
            last_review_at = ?, next_review_at = ?
        WHERE id = ?
    """, (interval, round(ease_factor, 4), review_count,
          now.isoformat(), next_review.isoformat(), point_id))

    conn.execute("""
        INSERT INTO review_history (knowledge_point_id, review_time, remember_level)
        VALUES (?, ?, ?)
    """, (point_id, now.isoformat(), remember_level))

    conn.commit()
    conn.close()

    print(json.dumps({
        "point_id": point_id,
        "remember_level": remember_level,
        "new_interval_days": round(interval, 1),
        "next_review_at": next_review.strftime("%Y-%m-%d %H:%M"),
        "ease_factor": round(ease_factor, 2),
        "review_count": review_count,
    }, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

def get_statistics():
    conn = get_connection()

    total = conn.execute("SELECT COUNT(*) FROM knowledge_points").fetchone()[0]
    never_reviewed = conn.execute(
        "SELECT COUNT(*) FROM knowledge_points WHERE review_count = 0"
    ).fetchone()[0]

    now = datetime.now().isoformat()
    due_now = conn.execute(
        "SELECT COUNT(*) FROM knowledge_points WHERE next_review_at <= ?", (now,)
    ).fetchone()[0]

    total_reviews = conn.execute("SELECT COUNT(*) FROM review_history").fetchone()[0]
    sources_count = conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0]

    avg_ease = conn.execute(
        "SELECT AVG(ease_factor) FROM knowledge_points WHERE review_count > 0"
    ).fetchone()[0]

    level_dist = conn.execute("""
        SELECT remember_level, COUNT(*) as cnt
        FROM review_history
        GROUP BY remember_level
        ORDER BY remember_level
    """).fetchall()

    conn.close()

    print(json.dumps({
        "total_knowledge_points": total,
        "never_reviewed": never_reviewed,
        "due_for_review": due_now,
        "total_reviews_done": total_reviews,
        "total_sources": sources_count,
        "average_ease_factor": round(avg_ease, 2) if avg_ease else None,
        "review_level_distribution": {str(r[0]): r[1] for r in level_dist},
    }, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
# YouTube subtitle fetching
# ---------------------------------------------------------------------------

def _clean_subtitle(raw_text):
    """Strip VTT/SRT timestamps, headers, tags, and deduplicate lines."""
    lines = raw_text.strip().split('\n')
    text_lines = []
    prev = ''
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if line.startswith(('WEBVTT', 'NOTE', 'Kind:', 'Language:')):
            continue
        if '-->' in line:
            continue
        if re.match(r'^\d+$', line):
            continue
        if re.match(r'^\d{2}:\d{2}', line):
            continue
        line = re.sub(r'<[^>]+>', '', line)
        line = re.sub(r'\{[^}]+\}', '', line)
        line = line.strip()
        if line and line != prev:
            text_lines.append(line)
            prev = line
    return '\n'.join(text_lines)


def fetch_youtube(url):
    """Fetch YouTube subtitles. Tries youtube_transcript_api then yt-dlp."""
    match = re.search(r'(?:v=|/v/|youtu\.be/|/embed/)([a-zA-Z0-9_-]{11})', url)
    if not match:
        print(json.dumps({"error": "Could not extract video ID from URL"}))
        return

    video_id = match.group(1)
    title = video_id
    content = None
    method = None
    lang_used = None

    # --- try to get title via yt-dlp ---
    try:
        r = subprocess.run(
            ['yt-dlp', '--print', 'title', '--no-download', url],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode == 0 and r.stdout.strip():
            title = r.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # --- method 1: youtube_transcript_api ---
    try:
        from youtube_transcript_api import YouTubeTranscriptApi  # noqa
        lang_prefs = ['zh-Hans', 'zh-CN', 'zh', 'zh-TW', 'en', 'ja', 'ko']
        try:
            entries = YouTubeTranscriptApi.get_transcript(video_id, languages=lang_prefs)
            content = '\n'.join(e['text'] for e in entries)
            method = 'youtube_transcript_api'
        except Exception:
            tlist = YouTubeTranscriptApi.list_transcripts(video_id)
            for t in tlist:
                entries = t.fetch()
                content = '\n'.join(e['text'] for e in entries)
                lang_used = t.language_code
                method = 'youtube_transcript_api'
                break
    except ImportError:
        pass
    except Exception:
        pass

    # --- method 2: yt-dlp subtitle download ---
    if content is None:
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                subprocess.run([
                    'yt-dlp', '--write-sub', '--write-auto-sub',
                    '--sub-lang', 'zh,en,ja',
                    '--skip-download', '--no-check-certificates',
                    '-o', os.path.join(tmpdir, 'video'),
                    url,
                ], capture_output=True, text=True, timeout=120)

                sub_files = sorted(
                    [f for f in os.listdir(tmpdir)
                     if f.endswith(('.vtt', '.srt', '.json3'))],
                    key=lambda x: ('zh' in x, not x.endswith('.json3')),
                    reverse=True,
                )
                if sub_files:
                    with open(os.path.join(tmpdir, sub_files[0]), 'r',
                              encoding='utf-8', errors='replace') as f:
                        raw = f.read()
                    content = _clean_subtitle(raw)
                    method = 'yt-dlp'
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    if content is None:
        print(json.dumps({
            "error": ("Failed to fetch subtitles. "
                      "Install one: pip install youtube-transcript-api  or  pip install yt-dlp"),
            "video_id": video_id,
            "url": url,
        }))
        return

    print(json.dumps({
        "video_id": video_id,
        "title": title,
        "content": content,
        "method": method,
        "char_count": len(content),
    }, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Raw SQL (escape hatch)
# ---------------------------------------------------------------------------

def execute_sql(sql):
    conn = get_connection()
    is_select = sql.upper().strip().startswith('SELECT')
    if is_select:
        rows = conn.execute(sql).fetchall()
        output = [dict(row) for row in rows]
        print(json.dumps({"status": "ok", "results": output}, ensure_ascii=False, indent=2))
    else:
        conn.execute(sql)
        conn.commit()
        print(json.dumps({"status": "ok", "rows_affected": conn.total_changes}))
    conn.close()


# ---------------------------------------------------------------------------
# CLI dispatcher
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) < 2:
        cmds = ("init, add-source, add-points, search-sources, list-sources, "
                "get-source-content, get-review, record-review, search, stats, "
                "fetch-youtube, execute-sql")
        print(f"Usage: python executor.py <command> ['<json_args>']\nCommands: {cmds}")
        sys.exit(1)

    command = sys.argv[1]
    args = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}

    commands = {
        'init':               lambda: initialize_database(),
        'add-source':         lambda: add_source(
            args['title'], args['type'],
            args.get('local_path'), args.get('url'), args.get('tags')
        ),
        'add-points':         lambda: add_knowledge_points(args['source_id'], args['points']),
        'search-sources':     lambda: search_sources(args['query'], args.get('limit', 10)),
        'list-sources':       lambda: list_sources(),
        'get-source-content': lambda: get_source_content(args['source_id']),
        'get-review':         lambda: get_review_candidates(
            args.get('limit', 5), args.get('topic')
        ),
        'record-review':      lambda: record_review(args['point_id'], args['level']),
        'search':             lambda: search_knowledge(args['query'], args.get('limit', 10)),
        'stats':              lambda: get_statistics(),
        'fetch-youtube':      lambda: fetch_youtube(args['url']),
        'execute-sql':        lambda: execute_sql(args['sql']),
    }

    if command not in commands:
        print(json.dumps({"error": f"Unknown command: {command}"}))
        sys.exit(1)

    try:
        commands[command]()
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)


if __name__ == '__main__':
    main()