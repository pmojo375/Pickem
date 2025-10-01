# Team Logos Setup

## Directory Structure
```
pickem/
├── static/
│   └── logos/
│       ├── 2.png      (Team logos named by CFBD ID)
│       ├── 5.png
│       └── ...
├── cfb/
│   └── templatetags/
│       ├── __init__.py
│       └── cfb_tags.py  (Custom template filter for logo URLs)
└── ...
```

## How It Works

### 1. Static Files Configuration
In `settings.py`:
```python
STATIC_URL = 'static/'
STATICFILES_DIRS = [
    BASE_DIR / 'static',
]
```

### 2. Template Tag
Created `cfb_tags.py` with a custom filter:
```python
@register.filter
def team_logo_url(team):
    """Return the static URL for a team's logo based on CFBD ID"""
    if team and team.cfbd_id:
        return f'logos/{team.cfbd_id}.png'
    return None
```

### 3. Usage in Templates
Load the tags and use them:
```django
{% load static %}
{% load cfb_tags %}

{% if team|team_logo_url %}
  <img src="{% static team|team_logo_url %}" alt="{{ team.name }}" height="24" />
{% endif %}
{{ team.name }}
```

## Pages Updated
- ✅ **Picks Page** - Shows logos for away/home teams
- ✅ **Live Page** - Shows logos for away/home/picked teams
- ✅ **Settings Page** - Shows logos in the games table

## Notes
- Logos are only displayed if the team has a `cfbd_id`
- Logo files must be named as `{cfbd_id}.png`
- Logos gracefully fall back to just team names if file is missing

