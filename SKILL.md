---
name: review_helper
description: Help users break down learning chapters/courses into knowledge points and review them using spaced repetition based on the Ebbinghaus forgetting curve (SM-2 algorithm). Reference materials are stored in `local_reference/`, knowledge points and review data in `data.sqlite3`, Python scripts in `scripts/`.
---

# Review Helper

A spaced repetition learning assistant. It breaks down study materials into knowledge points and schedules reviews using the SM-2 algorithm (based on the Ebbinghaus forgetting curve).

## Directory Structure

- `SKILL.md` — This instruction file
- `scripts/executor.py` — CLI tool for all database operations
- `local_reference/` — Saved reference materials (original content, keep as complete as possible)
- `data.sqlite3` — SQLite database

## Database Schema

### sources

| Column     | Type      | Description                                  |
|------------|-----------|----------------------------------------------|
| id         | INTEGER PK| Auto-increment ID                            |
| title      | TEXT      | Source title                                 |
| type       | TEXT      | One of: book, course, url, file, youtube     |
| local_path | TEXT      | Relative path in local_reference/            |
| url        | TEXT      | Original URL (if applicable)                 |
| tags       | TEXT      | Comma-separated tags/keywords for this source|
| created_at | TIMESTAMP | Creation time                                |

### knowledge_points

| Column         | Type      | Description                                       |
|----------------|-----------|---------------------------------------------------|
| id             | INTEGER PK| Auto-increment ID                                 |
| source_id      | INTEGER FK| References sources.id                             |
| title          | TEXT      | Short title of the knowledge point                |
| content        | TEXT      | Core concept description (NOT a Q&A, just the key idea) |
| chapter        | TEXT      | Chapter/section name                              |
| tags           | TEXT      | Comma-separated tags                              |
| created_at     | TIMESTAMP | Creation time                                     |
| next_review_at | TIMESTAMP | Next scheduled review time                        |
| interval_days  | REAL      | Current review interval in days                   |
| ease_factor    | REAL      | SM-2 ease factor (default 2.5)                    |
| review_count   | INTEGER   | Number of consecutive successful reviews          |
| last_review_at | TIMESTAMP | Last review time                                  |

### review_history

| Column             | Type      | Description                        |
|--------------------|-----------|------------------------------------|
| id                 | INTEGER PK| Auto-increment ID                  |
| knowledge_point_id | INTEGER FK| References knowledge_points.id     |
| review_time        | TIMESTAMP | When the review happened           |
| remember_level     | INTEGER   | 0-5 rating                         |

## CLI Usage

All commands: `python {SKILL_DIR}/scripts/executor.py <command> ['<json_args>']`

`{SKILL_DIR}` refers to the directory containing this SKILL.md file.

| Command              | JSON Args | Description |
|----------------------|-----------|-------------|
| `init`               | (none) | Initialize database and directories |
| `add-source`         | `{"title":"...", "type":"book\|course\|url\|file\|youtube", "local_path":"...", "url":"...", "tags":"..."}` | Register a source with tags |
| `add-points`         | `{"source_id":N, "points":[{"title":"...", "content":"...", "chapter":"...", "tags":"..."}]}` | Add knowledge points |
| `search-sources`     | `{"query":"...", "limit":10}` | Search sources by title/tags/url; returns existing_tags for reuse |
| `list-sources`       | (none) | List all sources with all_tags summary |
| `get-source-content` | `{"source_id":N}` | Read & return full content of a source's local file |
| `get-review`         | `{"limit":5, "topic":"..."}` (all optional) | Get review candidates by urgency |
| `record-review`      | `{"point_id":N, "level":N}` | Record a review result (level 0-5) |
| `search`             | `{"query":"...", "limit":10}` | Search knowledge points by keyword |
| `stats`              | (none) | Show review statistics |
| `fetch-youtube`      | `{"url":"..."}` | Fetch YouTube subtitles (returns title + content) |
| `execute-sql`        | `{"sql":"..."}` | Run raw SQL (escape hatch) |

## Setup (First Time)

Before first use, initialize the database:

```
python {SKILL_DIR}/scripts/executor.py init
```

This creates the tables and the `local_reference/` directory. It is safe to run multiple times (idempotent).

## Workflows

### Workflow 1: Import Knowledge

**Trigger**: User wants to learn from a book chapter, course, URL, YouTube video, or local file. Keywords: "学习", "导入", "添加", "learn", "import".

**Steps**:

1. **Search existing sources for tag reuse**:
   ```
   python {SKILL_DIR}/scripts/executor.py search-sources '{"query":"<topic_keyword>"}'
   ```
   Review the `existing_tags` in the response. When creating the new source, **reuse matching tags** to keep tagging consistent. Add new tags only when no existing tag fits.

2. **Obtain the content**:
   - **YouTube URL**: Run `fetch-youtube` first (see Workflow 1a below).
   - **Other URL**: Use `WebFetch` to retrieve the content.
   - **Local file**: Use `Read` to read it.
   - **User pastes text**: Use it directly.

3. **Save reference material** — save the **complete, original** content:
   - Write to `{SKILL_DIR}/local_reference/<slug>.md` (or `.txt`)
   - Use a meaningful slug (lowercase, underscores, e.g. `python_decorators.md`)
   - Keep the content as intact as possible; do NOT summarize or truncate. This file is the authoritative reference used later for detailed explanations.

4. **Register the source** (use tags from step 1):
   ```
   python {SKILL_DIR}/scripts/executor.py add-source '{"title":"<title>", "type":"<type>", "local_path":"local_reference/<slug>.md", "url":"<url_or_empty>", "tags":"<comma,separated,tags>"}'
   ```
   Returns `{"source_id": N}`.

5. **Extract knowledge points** from the content. For each point, create:
   - `title`: Short descriptive title (e.g. "Python装饰器的本质")
   - `content`: The core concept or key idea described clearly and concisely. This is **NOT a question and NOT an answer** — it is a factual statement of what the user should understand. During review, the AI will generate questions dynamically from this.
   - `chapter`: Chapter or section name
   - `tags`: Comma-separated relevant tags (reuse from source tags when possible)

   **Extraction guidelines**:
   - Each point should capture ONE atomic concept
   - Describe the concept clearly enough that the AI can later formulate good questions from it
   - Aim for 5-15 points per chapter depending on density
   - Include both conceptual knowledge and practical/applied knowledge

6. **Save knowledge points**:
   ```
   python {SKILL_DIR}/scripts/executor.py add-points '{"source_id":<id>, "points":[...]}'
   ```

7. **Confirm**: Show the user the count and list of knowledge point titles.

#### Workflow 1a: YouTube Import

When the user provides a YouTube link:

1. **Fetch subtitles**:
   ```
   python {SKILL_DIR}/scripts/executor.py fetch-youtube '{"url":"<youtube_url>"}'
   ```
   This tries `youtube_transcript_api` then `yt-dlp`. If both fail, ask the user to install one:
   ```
   pip install youtube-transcript-api
   ```
   or
   ```
   pip install yt-dlp
   ```

2. The response includes `title` and `content` (subtitle text). Save the **full subtitle text** to `local_reference/` and continue with Workflow 1 step 4 onwards, using `type: "youtube"`.

### Workflow 2: Review Knowledge

**Trigger**: User asks to review. Keywords: "复习", "review", "今天复习什么", "what to review".

**Steps**:

1. **Get candidates**:
   ```
   python {SKILL_DIR}/scripts/executor.py get-review '{"limit":5}'
   ```
   Or with a topic:
   ```
   python {SKILL_DIR}/scripts/executor.py get-review '{"limit":5, "topic":"<topic>"}'
   ```

2. **Present ONE knowledge point at a time** — for each candidate:

   a. Read the knowledge point's `content` (the core concept).

   b. **Formulate a question dynamically** based on the content. You may also reference the source file for richer context. Vary question types across reviews:
      - Conceptual: "什么是 X？它的核心原理是？"
      - Applied: "在什么场景下会用到 X？"
      - Comparative: "X 和 Y 有什么区别？"
      - Debugging: "如果 X 出了问题，可能的原因是？"

   c. Show the question and context (source title, chapter, tags) to the user.
      Do NOT reveal the concept content yet.

3. **Evaluate the user's response**:
   - If the answer is **correct and complete** → acknowledge and move to rating.
   - If the answer is **vague or partial** → **ask follow-up questions** to probe deeper. For example:
     - "你能再具体说说 X 的原理吗？"
     - "那如果遇到 Y 的情况呢？"
   - If the user **doesn't remember at all** → reveal the concept content and offer detailed explanation (see Workflow 3).
   - Continue the conversation until you have enough signal to judge their understanding.

4. **Judge the remember level** (0-5) — infer from the conversation quality. Do NOT always ask the user to self-rate; use your judgment based on response accuracy, speed of recall, and depth of understanding:

   | Level | Criteria |
   |-------|----------|
   | 0 | 完全不记得，无法给出任何相关信息 |
   | 1 | 几乎不记得，回答完全错误但隐约有印象 |
   | 2 | 有点印象，回答部分正确但关键点错误 |
   | 3 | 基本想起来了，但需要很多提示和追问才说清楚 |
   | 4 | 回答正确，有小的遗漏或犹豫 |
   | 5 | 完美回忆，快速且准确地回答，理解深入 |

   If uncertain, you MAY ask the user: "你觉得这个知识点你掌握得怎么样？（0-5）"

5. **Record the result**:
   ```
   python {SKILL_DIR}/scripts/executor.py record-review '{"point_id":<id>, "level":<0-5>}'
   ```

6. **Show brief feedback**: The core concept, next review time, and encouragement.

7. **Continue**: Ask if user wants to review the next point. Repeat steps 2-6.

### Workflow 3: Detailed Explanation

**Trigger**: During review the user doesn't remember and wants a detailed explanation. Or user explicitly asks: "详细讲解", "explain", "展开讲讲".

**Steps**:

1. **Load the source file**. First try via CLI:
   ```
   python {SKILL_DIR}/scripts/executor.py get-source-content '{"source_id":<id>}'
   ```
   - If the content is returned successfully, use it for the explanation.
   - If the file is very large (>100KB), use `Read` tool with `offset`/`limit`, or use `Grep` to locate the relevant section (search by chapter title or keywords from the knowledge point).

2. **Give a thorough explanation** based on the original source material:
   - Explain the concept in detail
   - Provide examples from the source
   - Connect it to related knowledge points if applicable
   - Use analogies to aid understanding

3. After explanation, this counts as a review with level 0-2 (depending on how much the user remembered before asking). Record accordingly.

### Workflow 4: Search / Browse / Statistics

**Trigger**: User searches for knowledge, asks about stats, or browses sources. Keywords: "搜索", "查找", "统计", "search", "stats".

- **Search knowledge points**: `python {SKILL_DIR}/scripts/executor.py search '{"query":"<keyword>"}'`
- **Search sources**: `python {SKILL_DIR}/scripts/executor.py search-sources '{"query":"<keyword>"}'`
- **Statistics**: `python {SKILL_DIR}/scripts/executor.py stats`
- **List sources**: `python {SKILL_DIR}/scripts/executor.py list-sources`

Present results in a clear formatted table.

## SM-2 Algorithm Reference

The review scheduling uses the SM-2 (SuperMemo 2) spaced repetition algorithm:

- **Level >= 3** (successful recall): Interval grows
  - 1st success: interval = 1 day
  - 2nd success: interval = 6 days
  - Subsequent: interval = previous_interval × ease_factor
- **Level < 3** (failed recall): Interval resets to 1 day, review_count resets to 0
- **Ease factor** adjusts each review: `EF' = EF + (0.1 - (5-q) × (0.08 + (5-q) × 0.02))`, minimum 1.3
- **Urgency score** = overdue_days / interval_days — higher means more overdue, higher selection probability

