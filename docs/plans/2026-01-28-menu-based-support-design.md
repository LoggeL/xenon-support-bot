# Menu-Based Support System Design

## Overview

Replace the `/support` slash command with a channel-based menu system. A sticky menu message with a button opens a modal for questions. All responses are ephemeral. Unanswered questions link to community support. Full analytics tracking via SQLite.

## User Flow

```
1. Admin: /setup-support-menu #support #community-help
   → Bot posts menu embed with "Ask a Question" button

2. User clicks "❓ Ask a Question"
   → Modal opens with text input

3. User submits question
   → Ephemeral "Processing..." message
   → Question logged to DB
   → Agent runs, tool calls logged

4. Agent returns response
   → Ephemeral answer embed
   → Buttons: [✅ Resolved] [💬 Community Support]

5a. User clicks "✅ Resolved"
    → DB: answered=true
    → Buttons disabled

5b. User clicks "💬 Community Support"
    → DB: community_support_clicked=true
    → Link to community channel
```

## Database Schema

SQLite database at `data/analytics.db`:

```sql
CREATE TABLE questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    question TEXT NOT NULL,
    answered BOOLEAN NOT NULL DEFAULT FALSE,
    community_support_clicked BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE tool_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id INTEGER NOT NULL REFERENCES questions(id),
    tool_name TEXT NOT NULL,
    arguments TEXT,
    result TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_questions_guild ON questions(guild_id);
CREATE INDEX idx_questions_answered ON questions(answered);
CREATE INDEX idx_tool_calls_question ON tool_calls(question_id);
```

## Server Configuration

Extended fields in `ServerConfig`:

```python
@dataclass
class ServerConfig:
    guild_id: int
    support_role_id: int | None = None              # keep for future use
    ticket_channel_id: int | None = None            # keep for future use
    support_channel_id: int | None = None           # NEW: menu channel
    menu_message_id: int | None = None              # NEW: menu message ID
    community_support_channel_id: int | None = None # NEW: fallback channel
```

## Commands

### New Commands

```
/setup-support-menu <channel> [community_channel]
    Posts menu message, stores config
    Admin only

/support-analytics [days=7]
    Shows: total questions, answer rate, top unanswered topics
    Admin only

/support-unanswered [days=7] [limit=10]
    Lists recent unanswered questions
    Admin only
```

### Keep

- `/scrape` - Update documentation
- `/support-config show` - View settings

### Remove

- `/support` - Replaced by menu button
- `/support-config support-role` - No tickets
- `/support-config ticket-channel` - No tickets
- `/support-config processing-visibility` - Always ephemeral
- `/clear` - Not needed

## New Components

### File Structure

```
src/
├── analytics.py              # NEW: SQLite wrapper
├── views/
│   └── support_menu.py       # NEW: Menu, Modal, Response views
├── bot.py                    # MODIFY: New commands
├── server_config.py          # MODIFY: New fields
└── agent/
    └── runner.py             # MODIFY: Tool call callback
```

### Analytics Module

```python
class Analytics:
    async def log_question(guild_id, user_id, channel_id, question) -> int
    async def log_tool_call(question_id, tool_name, arguments, result)
    async def mark_answered(question_id)
    async def mark_community_support(question_id)
    async def get_unanswered(guild_id, days=7, limit=10) -> list[dict]
    async def get_stats(guild_id, days=7) -> dict
```

### Views Module

```python
class SupportMenuView(discord.ui.View):
    """Persistent view with 'Ask a Question' button"""
    timeout = None  # Never expires

class SupportQuestionModal(discord.ui.Modal):
    """Modal with single text input for question"""
    question: TextInput(style=paragraph, max_length=1000)

class SupportResponseView(discord.ui.View):
    """Response buttons: Resolved, Community Support"""
    timeout = 300  # 5 minutes
```

### Menu Embed

```
┌─────────────────────────────────────────┐
│  🤖 Xenon Support                       │
│                                         │
│  Have a question about Xenon?           │
│  Click the button below and our AI      │
│  will try to help based on the          │
│  official documentation.                │
│                                         │
│  [❓ Ask a Question]                    │
└─────────────────────────────────────────┘
```

## Agent Runner Integration

Add optional callback for tool call logging:

```python
async def run(
    self,
    question: str,
    context: list[str] | None = None,
    on_tool_call: Callable[[str, dict, dict], Awaitable[None]] | None = None
) -> AsyncGenerator[AgentStep, None]:
    # After each tool call:
    if on_tool_call:
        await on_tool_call(tool_name, arguments, result)
```

## Migration

- No data migration needed
- New config fields default to None (backwards compatible)
- Old `/support` command removed - users use menu instead
