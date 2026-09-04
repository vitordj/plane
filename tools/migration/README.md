# Plane Migration Tools

Utility scripts for migrating workspace data between Plane installations.

## Migrated Entities Status

| Entity / Resource           | Migrated? | Notes / Behavior                                                                                                              |
| :-------------------------- | :-------: | :---------------------------------------------------------------------------------------------------------------------------- |
| **Projects**                |  ✅ Yes   | Recreated with the same name, identifier, and description.                                                                    |
| **Project Settings**        |  ✅ Yes   | Toggles for cycle, module, page, views, and intake layouts are synced.                                                        |
| **Workflow States**         |  ✅ Yes   | Syncs custom project workflow states (name, color, group). Maps issues to their correct state.                                |
| **Project Labels**          |  ✅ Yes   | Syncs custom project labels (name, color, description). Maps issues to their correct labels.                                  |
| **Cycles**                  |  ✅ Yes   | Recreated with name, description, start date, and end date.                                                                   |
| **Modules**                 |  ✅ Yes   | Recreated with name, description, status, start date, and end date.                                                           |
| **Issues / Work Items**     |  ✅ Yes   | Copied with title, description (HTML), and priority. Mapped to their respective state, labels, cycle, modules, and assignees. |
| **Users / Members**         |  ✅ Yes   | Workspace members are matched by email. If they don't exist, an invitation is sent to their email with their mapped role.     |
| **Project Memberships**     |  ✅ Yes   | Syncs project members with their correct matching role.                                                                       |
| **Estimates**               |  ✅ Yes   | Recreates project estimate point systems and links issues to their respective `estimate_point`.                               |
| **Comments**                |  ✅ Yes   | Syncs issue comment threads, mapping commentator user accounts where possible.                                                |
| **Issue Links**             |  ✅ Yes   | Syncs external links (GitHub PRs, reference URLs) associated with issues.                                                     |
| **Intake / Inbox Items**    |  ✅ Yes   | Syncs automatically when issues are migrated in their respective `triage` workflow states.                                    |
| **Stand-alone Attachments** |   ❌ No   | Downloads raw sidecar files from the source, uploads them to the target workspace generic asset store, and links to issues.   |
| **Views**                   |   ❌ No   | Saved views are not migrated. They require session authentication instead of API tokens.                                      |
| **Project Pages**           |   ❌ No   | Pages are not migrated. They require session authentication instead of API tokens.                                            |
| **Embedded Images / Files** |   ❌ No   | Inline images/files in issue descriptions are not re-uploaded; original URLs are preserved as-is in the description HTML.     |
| **User Stickies**           |   ❌ No   | Workspace sticky notes are not migrated.                                                                                      |

---

## Part 1: Pre-create User Accounts (On the Server hosting the new Plane database)

To assign issues, module leads, and project roles to the correct users, their email accounts must exist in the target database first. We can automate querying the old server and seeding them using `create_users.py`.

Because this script directly writes to the database using the Django ORM, **it must be run inside the new Plane container on your server**.

### Step A: Run from your server shell (via Docker pipe)

Run this command on your hosting server terminal where the container is running:

```bash
docker exec -i <new-plane-api-container-name> python3 - < tools/migration/create_users.py
```

_(If you are using Coolify, replace `<new-plane-api-container-name>` with the name of the container running your new Plane `api` service)._

### Step B (Alternative): Manual Copy-Paste inside the container

If you are already inside the container shell (`docker exec -it <container> sh`):

1. Run `cat > create_users.py` inside the container shell.
2. Paste the contents of `tools/migration/create_users.py` into the terminal.
3. Press `Ctrl + D` to save the file.
4. Run `python3 create_users.py`.
5. Remove the temp file when done: `rm create_users.py`.

### First sign-in

Pre-created accounts have **no password**. The account exists so that issues,
leads and roles can be mapped to it; the person it belongs to has not signed in
yet, and a password shared by every migrated account would be a credential
anyone reading this repository could use.

They sign in the same way accounts created by an identity provider do:

- **Entra ID** — the usual path on this install. The account is matched by
  email on the first sign-in (see [`docs/entra-directory-sync.md`](../../docs/entra-directory-sync.md)).
- **Magic link** — email code, if the instance has it enabled.

Both paths mark the account as having chosen its own credential. If the
instance allows password sign-in at all, the person sets one from their profile
after the first sign-in.

### If you ran this script before this version

Earlier revisions gave every account they created the same hard-coded password,
which was also printed in this file. Those credentials must be treated as
public: anyone who saw either could have signed in as any migrated person who
had not yet signed in themselves.

On each environment where the old script ran, invalidate them. Run inside the
API container, replacing the date with the day the script ran (or listing the
emails it created):

```bash
docker exec -it <plane-api-container> python3 manage.py shell
```

```python
from django.utils.dateparse import parse_datetime
from plane.db.models import User

# Everything the script created that day, or: User.objects.filter(email__in=[...])
migrated = User.objects.filter(created_at__date="2026-01-01")

for user in migrated:
    if user.last_login is None:  # never signed in: nothing of theirs is lost
        user.set_unusable_password()
        user.is_password_autoset = True
        user.save(update_fields=["password", "is_password_autoset"])
        print("invalidated", user.email)
    else:
        print("REVIEW — signed in at least once:", user.email, user.last_login)
```

Accounts that did sign in need a look before you touch them: either the person
signed in legitimately and has since chosen their own password, or somebody
used the shared one. Check the authentication logs for those emails and, when
in doubt, invalidate the password too and tell the person to sign in through
Entra ID.

---

## Part 2: Run the Migration Script (On your local machine / client machine)

The data migration script (`migrate_data.py`) communicates purely over Plane's HTTP APIs, meaning **you can run it on your local developer machine** without having server access.

### 1. Install Dependencies

Make sure you have python virtual environment set up and dependencies installed:

```bash
# Create a virtual environment in the tools directory
python3 -m venv tools/migration/.venv

# Activate the virtual environment
source tools/migration/.venv/bin/activate

# Install dependencies
pip install requests python-dotenv
```

### 2. Configure Environment Variables

Make sure to add the following block to your `.env` file at the root of the project:

```env
# Migration Settings
MIGRATION_OLD_PLANE_URL="https://your-old-plane-domain.com"
MIGRATION_OLD_API_TOKEN="your-old-api-token"
MIGRATION_OLD_WORKSPACE_SLUG="your-old-workspace-slug"

MIGRATION_NEW_PLANE_URL="https://your-new-orca-domain.com"
MIGRATION_NEW_API_TOKEN="your-new-api-token"
MIGRATION_NEW_WORKSPACE_SLUG="your-new-workspace-slug"
```

### 3. Run the Script

Ensure your virtual environment is active, then run the script:

```bash
# If not already activated:
source tools/migration/.venv/bin/activate

# Run the script
python tools/migration/migrate_data.py
```
